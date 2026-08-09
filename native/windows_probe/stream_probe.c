/* Physical-camera smoke test for the proven DevDll ABI. No GUI or decoder. */
#define WIN32_LEAN_AND_MEAN
#include <windows.h>
#include <wincred.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

typedef void *(__stdcall *dev_init2_fn)(const char *);
typedef void (__stdcall *dev_unit_fn)(void **);
typedef int (__stdcall *dev_set_callback_fn)(void *, void *, void *);
typedef int (__stdcall *dev_put_ip_fn)(void *, const char *, const char *, int);
typedef int (__stdcall *dev_put_auth_fn)(void *, const char *, const char *);
typedef int (__stdcall *dev_connect_fn)(void *, int);
typedef int (__stdcall *dev_handle_fn)(void *);
typedef int (__stdcall *dev_handle_int_fn)(void *, int);
typedef int (__stdcall *dev_cgi_fn)(void *, const char *, int);
typedef int (__stdcall *dev_is_connected_fn)(void *);
typedef void (__stdcall *raw_video_fn)(void *, unsigned int, void *);

typedef struct capture_state {
    FILE *stream;
    CRITICAL_SECTION lock;
    volatile LONG frames;
    volatile LONG invalid_frames;
    volatile LONG h265_frames;
    unsigned long long bytes;
} capture_state;

static char *wide_blob_to_utf8(const BYTE *blob, DWORD bytes) {
    if (!blob || !bytes || bytes % sizeof(wchar_t)) return NULL;
    int count = (int)(bytes / sizeof(wchar_t));
    int size = WideCharToMultiByte(CP_UTF8, WC_ERR_INVALID_CHARS,
        (const wchar_t *)blob, count, NULL, 0, NULL, NULL);
    if (size <= 0) return NULL;
    char *output = (char *)LocalAlloc(LMEM_FIXED, (SIZE_T)size + 1);
    if (!output) return NULL;
    WideCharToMultiByte(CP_UTF8, WC_ERR_INVALID_CHARS, (const wchar_t *)blob,
        count, output, size, NULL, NULL);
    output[size] = '\0';
    return output;
}

static char *read_keyring_value(const wchar_t *service, const wchar_t *username) {
    PCREDENTIALW credential = NULL;
    wchar_t compound[512];
    const wchar_t *targets[2] = {service, compound};
    _snwprintf(compound, sizeof(compound) / sizeof(compound[0]), L"%ls@%ls", username, service);
    compound[(sizeof(compound) / sizeof(compound[0])) - 1] = L'\0';
    for (int index = 0; index < 2; ++index) {
        if (!CredReadW(targets[index], CRED_TYPE_GENERIC, 0, &credential)) continue;
        int matches = credential->UserName && _wcsicmp(credential->UserName, username) == 0;
        char *value = matches ? wide_blob_to_utf8(
            credential->CredentialBlob, credential->CredentialBlobSize) : NULL;
        CredFree(credential);
        credential = NULL;
        if (value) return value;
    }
    return NULL;
}

static void free_secret(char **value) {
    if (value && *value) {
        SecureZeroMemory(*value, strlen(*value));
        LocalFree(*value);
        *value = NULL;
    }
}

static int resolve(HMODULE library, const char *name, void *output, size_t size) {
    FARPROC symbol = GetProcAddress(library, name);
    if (!symbol || size != sizeof(symbol)) return 0;
    memcpy(output, &symbol, sizeof(symbol));
    return 1;
}

static void __stdcall on_raw_video(void *frame, unsigned int metadata, void *context) {
    (void)metadata;
    capture_state *state = (capture_state *)context;
    if (!frame || !state) return;
    const uint8_t *bytes = (const uint8_t *)frame;
    uint32_t length = 0;
    memcpy(&length, bytes + 0x10, sizeof(length));
    if (!length || length > 16U * 1024U * 1024U) {
        InterlockedIncrement(&state->invalid_frames);
        return;
    }
    uint8_t frame_type = bytes[4];
    if (frame_type == 0x10 || frame_type == 0x11) {
        InterlockedIncrement(&state->h265_frames);
        return;
    }
    EnterCriticalSection(&state->lock);
    size_t written = fwrite(bytes + 0x20, 1, length, state->stream);
    if (written == length) {
        state->bytes += written;
        InterlockedIncrement(&state->frames);
    } else {
        InterlockedIncrement(&state->invalid_frames);
    }
    LeaveCriticalSection(&state->lock);
}

int main(int argc, char **argv) {
    if (argc != 4 || strcmp(argv[1], "--physical-stream") != 0) {
        fprintf(stderr, "usage: okam-stream-probe --physical-stream DevDll_925.dll output.h264\n");
        return 2;
    }
    int result = 1;
    HMODULE library = NULL;
    void *handle = NULL;
    capture_state capture = {0};
    char *route = NULL, *user = NULL, *password = NULL;
    char *address_a = NULL, *address_b = NULL, *port_text = NULL;
    dev_init2_fn init2 = NULL; dev_unit_fn unit = NULL;
    dev_set_callback_fn set_raw = NULL; dev_put_ip_fn put_ip = NULL;
    dev_put_auth_fn put_auth = NULL; dev_connect_fn connect_camera = NULL;
    dev_handle_fn stop = NULL, disconnect = NULL; dev_handle_int_fn start2 = NULL;
    dev_cgi_fn trans_cgi = NULL; dev_is_connected_fn is_connected = NULL;
    int connect_result = -9999;
    int connected = 0;

    library = LoadLibraryA(argv[2]);
    if (!library) { fprintf(stderr, "Unable to load DevDll (Windows error %lu)\n", GetLastError()); goto cleanup; }
#define RESOLVE(variable, name) if (!resolve(library, name, &variable, sizeof(variable))) { \
    fprintf(stderr, "Missing or incompatible export: %s\n", name); goto cleanup; }
    RESOLVE(init2, "dev_Init2"); RESOLVE(unit, "dev_Unit");
    RESOLVE(set_raw, "dev_SetOnUnVSample"); RESOLVE(put_ip, "dev_put_IP");
    RESOLVE(put_auth, "dev_put_auth"); RESOLVE(connect_camera, "dev_Connect");
    RESOLVE(is_connected, "dev_IsNetConnected"); RESOLVE(stop, "dev_Stop");
    RESOLVE(start2, "dev_Start2"); RESOLVE(trans_cgi, "dev_TransCGI");
    RESOLVE(disconnect, "dev_DisConnect");
#undef RESOLVE
    route = read_keyring_value(L"okam-ha-bridge-native-sdk", L"server-parameter");
    user = read_keyring_value(L"okam-ha-bridge-native-device", L"username");
    password = read_keyring_value(L"okam-ha-bridge-native-device", L"password");
    address_a = read_keyring_value(L"okam-ha-bridge-native-network", L"address-a");
    address_b = read_keyring_value(L"okam-ha-bridge-native-network", L"address-b");
    port_text = read_keyring_value(L"okam-ha-bridge-native-network", L"port");
    if (!route || !user || !password || !address_a || !address_b || !port_text) {
        fprintf(stderr, "Stored native configuration is incomplete\n"); goto cleanup;
    }
    char *port_end = NULL;
    long port = strtol(port_text, &port_end, 10);
    if (!port_end || *port_end || port < 1 || port > 65535) {
        fprintf(stderr, "Stored native port is invalid\n"); goto cleanup;
    }
    capture.stream = fopen(argv[3], "wb");
    if (!capture.stream) { fprintf(stderr, "Unable to create encoded stream output\n"); goto cleanup; }
    InitializeCriticalSection(&capture.lock);
    handle = init2(route);
    if (!handle) { fprintf(stderr, "dev_Init2 failed\n"); goto cleanup_lock; }
    /* Callback setters are void-like and leave EAX at zero on the official DLL. */
    set_raw(handle, (void *)on_raw_video, &capture);
    if (!put_ip(handle, address_a, address_b, (int)port) ||
        !put_auth(handle, user, password)) {
        fprintf(stderr, "Official DLL rejected network or authentication configuration\n"); goto cleanup_handle;
    }
    connect_result = connect_camera(handle, 2);
    for (int second = 0; second < 75 && !is_connected(handle); ++second) Sleep(1000);
    connected = is_connected(handle);
    if (!connected) { fprintf(stderr, "Camera did not connect within 75 seconds\n"); goto cleanup_handle; }
    stop(handle);
    start2(handle, 0);
    const char *live = "/livestream.cgi?streamid=10&substream=0&";
    trans_cgi(handle, live, (int)strlen(live));
    for (int second = 0; second < 45 && capture.frames < 8; ++second) Sleep(1000);
    const char *stop_live = "/livestream.cgi?streamid=16&substream=0&";
    trans_cgi(handle, stop_live, (int)strlen(stop_live));
    if (capture.frames < 1 || capture.bytes < 1024) {
        fprintf(stderr, "No usable H.264 frames arrived\n"); goto cleanup_handle;
    }
    result = 0;

cleanup_handle:
    if (handle) {
        if (set_raw) set_raw(handle, NULL, NULL);
        if (stop) stop(handle);
        if (disconnect) disconnect(handle);
        if (unit) unit(&handle);
    }
cleanup_lock:
    DeleteCriticalSection(&capture.lock);
    if (capture.stream) fclose(capture.stream);
cleanup:
    free_secret(&route); free_secret(&user); free_secret(&password);
    free_secret(&address_a); free_secret(&address_b); free_secret(&port_text);
    if (library) FreeLibrary(library);
    printf("{\"gui\":false,\"connect_result\":%d,\"connected\":%s,\"frames\":%ld,\"bytes\":%llu,\"invalid_frames\":%ld,\"h265_frames\":%ld,\"success\":%s}\n",
        connect_result, connected ? "true" : "false", capture.frames, capture.bytes,
        capture.invalid_frames, capture.h265_frames, result == 0 ? "true" : "false");
    return result;
}
