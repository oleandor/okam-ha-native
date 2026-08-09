/* Minimal, non-GUI 32-bit probe for the official Windows P2P library.
 *
 * This deliberately performs no network or camera operation. A connection
 * implementation is enabled only after the sanitized trace proves every ABI.
 */
#define WIN32_LEAN_AND_MEAN
#include <windows.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>

typedef uint32_t (__cdecl *get_version_fn)(void);

static const char *required_exports[] = {
    "P2PAPI_Initial",
    "P2PAPI_InitialWithServer",
    "P2PAPI_CreateInstance",
    "P2PAPI_Connect",
    "P2PAPI_SetAVDataCallBack",
    "P2PAPI_SetMessageCallBack",
    "P2PAPI_StartVideo",
    "P2PAPI_StopVideo",
    "P2PAPI_Close",
    "P2PAPI_DestroyInstance",
    NULL
};

static void json_string(const char *value) {
    putchar('"');
    while (*value) {
        unsigned char c = (unsigned char)*value++;
        if (c == '"' || c == '\\') putchar('\\');
        if (c >= 0x20) putchar(c);
    }
    putchar('"');
}

int main(int argc, char **argv) {
    const char *path = argc > 1 ? argv[1] : "P2PAPI.dll";
    HMODULE library = LoadLibraryA(path);
    if (!library) {
        fprintf(stderr, "Unable to load P2PAPI library (Windows error %lu)\n", GetLastError());
        return 2;
    }
    int missing = 0;
    printf("{\"library\":");
    json_string(path);
    printf(",\"network_calls\":false,\"exports\":{");
    for (size_t i = 0; required_exports[i]; ++i) {
        if (i) putchar(',');
        FARPROC symbol = GetProcAddress(library, required_exports[i]);
        json_string(required_exports[i]);
        printf(":%s", symbol ? "true" : "false");
        if (!symbol) missing = 1;
    }
    printf("}");
    get_version_fn get_version = (get_version_fn)GetProcAddress(library, "P2PAPI_GetAPIVersion");
    if (get_version) printf(",\"api_version\":%lu", (unsigned long)get_version());
    printf("}\n");
    FreeLibrary(library);
    return missing ? 3 : 0;
}
