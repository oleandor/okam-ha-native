/* Prove the official device DLL can initialize and tear down without GUI. */
#define WIN32_LEAN_AND_MEAN
#include <windows.h>
#include <wincred.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

typedef void *(__stdcall *dev_init2_fn)(const char *server_parameter);
typedef void (__stdcall *dev_unit_fn)(void **handle);

static const char *required_exports[] = {
    "dev_Init2", "dev_Unit", "dev_SetOnStatus", "dev_SetOnConnected",
    "dev_SetOnVSample", "dev_SetOnUnVSample", "dev_put_IP", "dev_put_auth",
    "dev_Connect", "dev_DisConnect", "dev_Start2", "dev_Stop", "dev_TransCGI", NULL
};

static char *wide_blob_to_utf8(const BYTE *blob, DWORD bytes) {
    if (!blob || !bytes || bytes % sizeof(wchar_t)) return NULL;
    int wide_count = (int)(bytes / sizeof(wchar_t));
    int output_bytes = WideCharToMultiByte(CP_UTF8, WC_ERR_INVALID_CHARS,
        (const wchar_t *)blob, wide_count, NULL, 0, NULL, NULL);
    if (output_bytes <= 0) return NULL;
    char *output = (char *)LocalAlloc(LMEM_FIXED, (SIZE_T)output_bytes + 1);
    if (!output) return NULL;
    WideCharToMultiByte(CP_UTF8, WC_ERR_INVALID_CHARS, (const wchar_t *)blob,
        wide_count, output, output_bytes, NULL, NULL);
    output[output_bytes] = '\0';
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

static int resolve(HMODULE library, const char *name, void *output, size_t output_size) {
    FARPROC symbol = GetProcAddress(library, name);
    if (!symbol || output_size != sizeof(symbol)) return 0;
    memcpy(output, &symbol, sizeof(symbol));
    return 1;
}

int main(int argc, char **argv) {
    const char *path = argc > 1 ? argv[1] : "DevDll_925.dll";
    HMODULE library = LoadLibraryA(path);
    if (!library) {
        fprintf(stderr, "Unable to load DevDll (Windows error %lu)\n", GetLastError());
        return 2;
    }
    int missing = 0;
    for (int i = 0; required_exports[i]; ++i) {
        if (!GetProcAddress(library, required_exports[i])) {
            fprintf(stderr, "Missing export: %s\n", required_exports[i]);
            missing = 1;
        }
    }
    if (missing) {
        FreeLibrary(library);
        return 3;
    }
    dev_init2_fn init2 = NULL;
    dev_unit_fn unit = NULL;
    if (!resolve(library, "dev_Init2", &init2, sizeof(init2)) ||
        !resolve(library, "dev_Unit", &unit, sizeof(unit))) {
        fprintf(stderr, "Unable to resolve lifecycle functions\n");
        FreeLibrary(library);
        return 4;
    }
    char *route = read_keyring_value(L"okam-ha-bridge-native-sdk", L"server-parameter");
    if (!route) {
        fprintf(stderr, "P2P server parameter is missing from Windows Credential Manager\n");
        FreeLibrary(library);
        return 5;
    }
    void *handle = init2(route);
    SecureZeroMemory(route, strlen(route));
    LocalFree(route);
    if (!handle) {
        fprintf(stderr, "dev_Init2 returned no handle\n");
        FreeLibrary(library);
        return 6;
    }
    unit(&handle);
    if (handle != NULL) {
        fprintf(stderr, "dev_Unit did not clear the handle\n");
        FreeLibrary(library);
        return 7;
    }
    FreeLibrary(library);
    puts("{\"gui\":false,\"network_calls\":false,\"init\":true,\"teardown\":true}");
    return 0;
}
