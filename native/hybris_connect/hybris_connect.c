#include <arpa/inet.h>
#include <dlfcn.h>
#include <errno.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/random.h>
#include <sys/time.h>
#include <time.h>
#include <unistd.h>

#include <hybris/common/binding.h>
#include <hybris/common/hooks.h>

#define MAX_FIELD_BYTES 4096U
#define CONNECT_TYPE_NORMAL 126
#define CONNECT_STATE_ONLINE 3

typedef void *(*client_create_fn)(const char *, const char *);
typedef int (*client_connect_fn)(void *, int, const char *, int);
typedef bool (*client_disconnect_fn)(void *);
typedef void (*client_destroy_fn)(void *);

static uintptr_t stack_guard;
extern void __stack_chk_fail(void);

static void *okam_hook(const char *symbol_name, const char *requester) {
    (void)requester;
    if (strcmp(symbol_name, "__stack_chk_guard") == 0) return &stack_guard;
    if (strcmp(symbol_name, "__stack_chk_fail") == 0) return (void *)&__stack_chk_fail;
    if (strcmp(symbol_name, "usleep") == 0) return (void *)&usleep;
    if (strcmp(symbol_name, "gettimeofday") == 0) return (void *)&gettimeofday;
    if (strcmp(symbol_name, "time") == 0) return (void *)&time;
    if (strcmp(symbol_name, "difftime") == 0) return (void *)&difftime;
    if (strcmp(symbol_name, "sleep") == 0) return (void *)&sleep;
    if (strcmp(symbol_name, "nanosleep") == 0) return (void *)&nanosleep;
    if (strcmp(symbol_name, "__vsprintf_chk") == 0 ||
        strcmp(symbol_name, "__vsnprintf_chk") == 0) {
        return dlsym(RTLD_DEFAULT, symbol_name);
    }
    return NULL;
}

static bool initialize_stack_guard(void) {
    ssize_t received;
    do {
        received = getrandom(&stack_guard, sizeof(stack_guard), 0);
    } while (received < 0 && errno == EINTR);
    return received == (ssize_t)sizeof(stack_guard) && stack_guard != 0;
}

static bool read_exact(void *buffer, size_t size) {
    unsigned char *cursor = buffer;
    while (size > 0) {
        size_t received = fread(cursor, 1, size, stdin);
        if (received == 0) return false;
        cursor += received;
        size -= received;
    }
    return true;
}

static char *read_field(void) {
    uint32_t network_size;
    if (!read_exact(&network_size, sizeof(network_size))) return NULL;
    uint32_t size = ntohl(network_size);
    if (size == 0 || size > MAX_FIELD_BYTES) return NULL;
    char *value = calloc((size_t)size + 1, 1);
    if (value == NULL || !read_exact(value, size)) {
        free(value);
        return NULL;
    }
    for (uint32_t i = 0; i < size; ++i) {
        if ((unsigned char)value[i] < 0x20) {
            free(value);
            return NULL;
        }
    }
    return value;
}

int main(int argc, char **argv) {
    if (argc != 2) {
        fputs("usage: okam-hybris-connect /path/to/libOKSMARTPPCS.so\n", stderr);
        return 2;
    }
    char *uid = read_field();
    char *service_parameter = read_field();
    if (uid == NULL || service_parameter == NULL || !initialize_stack_guard()) {
        free(uid);
        free(service_parameter);
        fputs("invalid native P2P input\n", stderr);
        return 3;
    }
    hybris_set_hook_callback(okam_hook);
    void *library = android_dlopen(argv[1], RTLD_LAZY | RTLD_LOCAL);
    if (library == NULL) {
        free(uid);
        free(service_parameter);
        fputs("official native P2P library could not be loaded\n", stderr);
        return 3;
    }

    client_create_fn client_create = (client_create_fn)android_dlsym(library, "client_create");
    client_connect_fn client_connect = (client_connect_fn)android_dlsym(library, "client_connect");
    client_disconnect_fn client_disconnect =
        (client_disconnect_fn)android_dlsym(library, "client_disconnect");
    client_destroy_fn client_destroy = (client_destroy_fn)android_dlsym(library, "client_destroy");
    if (client_create == NULL || client_connect == NULL ||
        client_disconnect == NULL || client_destroy == NULL) {
        android_dlclose(library);
        free(uid);
        free(service_parameter);
        fputs("official native P2P lifecycle API is incomplete\n", stderr);
        return 3;
    }

    void *client = client_create(uid, NULL);
    memset(uid, 0, strlen(uid));
    free(uid);
    int state = -1;
    bool connected = false;
    bool disconnected = false;
    if (client != NULL) {
        state = client_connect(client, CONNECT_TYPE_NORMAL, service_parameter, 0);
        connected = state == CONNECT_STATE_ONLINE;
        if (connected) disconnected = client_disconnect(client);
        client_destroy(client);
    }
    memset(service_parameter, 0, strlen(service_parameter));
    free(service_parameter);
    printf("{\"connected\":%s,\"connect_state\":%d,\"disconnected\":%s}\n",
           connected ? "true" : "false", state, disconnected ? "true" : "false");
    android_dlclose(library);
    return connected && disconnected ? 0 : 4;
}
