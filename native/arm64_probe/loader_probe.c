/* Run this *inside the supplied Bionic runtime*, not as a glibc workaround. */
#include <dlfcn.h>
#include <stdio.h>

static const char *required_symbols[] = {
    "PPCS_Initialize", "PPCS_Connect", "PPCS_Read", "PPCS_Write",
    "PPCS_Check", "PPCS_Close", "Java_com_vstarcam_JNIApi_init",
    "Java_com_vstarcam_JNIApi_create", "Java_com_vstarcam_JNIApi_connect",
    "Java_com_vstarcam_JNIApi_login", "Java_com_vstarcam_JNIApi_writeCgi", NULL
};

int main(int argc, char **argv) {
    if (argc != 2) {
        fprintf(stderr, "usage: okam-arm64-probe /path/to/libOKSMARTPPCS.so\n");
        return 2;
    }
    void *library = dlopen(argv[1], RTLD_NOW | RTLD_LOCAL);
    if (!library) {
        fprintf(stderr, "Bionic load failed: %s\n", dlerror());
        return 3;
    }
    int missing = 0;
    printf("{\"bionic_load\":true,\"symbols\":{");
    for (int i = 0; required_symbols[i]; ++i) {
        if (i) putchar(',');
        int present = dlsym(library, required_symbols[i]) != NULL;
        printf("\"%s\":%s", required_symbols[i], present ? "true" : "false");
        missing |= !present;
    }
    printf("}}\n");
    dlclose(library);
    return missing ? 4 : 0;
}
