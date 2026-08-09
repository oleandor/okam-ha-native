#include <errno.h>
#include <dlfcn.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>
#include <sys/random.h>
#include <sys/time.h>
#include <time.h>
#include <unistd.h>

#include <hybris/common/binding.h>
#include <hybris/common/hooks.h>

static uintptr_t stack_guard;
extern void __stack_chk_fail(void);

static void *okam_hook(const char *symbol_name, const char *requester) {
    (void)requester;
    if (strcmp(symbol_name, "__stack_chk_guard") == 0) {
        return &stack_guard;
    }
    if (strcmp(symbol_name, "__stack_chk_fail") == 0) {
        return (void *)&__stack_chk_fail;
    }
    if (strcmp(symbol_name, "usleep") == 0) {
        return (void *)&usleep;
    }
    if (strcmp(symbol_name, "gettimeofday") == 0) {
        return (void *)&gettimeofday;
    }
    if (strcmp(symbol_name, "time") == 0) {
        return (void *)&time;
    }
    if (strcmp(symbol_name, "difftime") == 0) {
        return (void *)&difftime;
    }
    if (strcmp(symbol_name, "sleep") == 0) {
        return (void *)&sleep;
    }
    if (strcmp(symbol_name, "nanosleep") == 0) {
        return (void *)&nanosleep;
    }
    if (strcmp(symbol_name, "__vsprintf_chk") == 0 ||
        strcmp(symbol_name, "__vsnprintf_chk") == 0) {
        return dlsym(RTLD_DEFAULT, symbol_name);
    }
    return NULL;
}

static int initialize_stack_guard(void) {
    ssize_t received;
    do {
        received = getrandom(&stack_guard, sizeof(stack_guard), 0);
    } while (received < 0 && errno == EINTR);
    return received == (ssize_t)sizeof(stack_guard) && stack_guard != 0;
}

static const char *required_symbols[] = {
    "PPCS_Initialize",
    "PPCS_Connect",
    "PPCS_Read",
    "PPCS_Write",
    "PPCS_Check",
    "PPCS_Close",
    "Java_com_vstarcam_JNIApi_init",
    "Java_com_vstarcam_JNIApi_create",
    "Java_com_vstarcam_JNIApi_connect",
    "Java_com_vstarcam_JNIApi_login",
    "Java_com_vstarcam_JNIApi_writeCgi",
    NULL,
};

int main(int argc, char **argv) {
    if (argc != 2) {
        fputs("usage: okam-hybris-probe /path/to/libOKSMARTPPCS.so\n", stderr);
        return 2;
    }

    if (!initialize_stack_guard()) {
        fputs("failed to initialize Android stack guard\n", stderr);
        return 5;
    }
    hybris_set_hook_callback(okam_hook);

    void *library = android_dlopen(argv[1], RTLD_LAZY | RTLD_LOCAL);
    if (library == NULL) {
        const char *error = android_dlerror();
        fprintf(stderr, "Android library load failed: %s\n",
                error != NULL ? error : "unknown loader error");
        return 3;
    }

    int missing = 0;
    fputs("{\"hybris_load\":true,\"symbols\":{", stdout);
    for (int i = 0; required_symbols[i] != NULL; ++i) {
        if (i != 0) {
            putchar(',');
        }
        int present = android_dlsym(library, required_symbols[i]) != NULL;
        printf("\"%s\":%s", required_symbols[i], present ? "true" : "false");
        missing |= !present;
    }
    fputs("}}\n", stdout);

    android_dlclose(library);
    return missing ? 4 : 0;
}
