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
#define MAX_COMMAND_BYTES 32768U
#define CONNECT_TYPE_NORMAL 126
#define CONNECT_STATE_ONLINE 3
#define COMMAND_CHANNEL 0
#define VIDEO_CHANNEL 1
#define LOGIN_RESPONSE 24577U
#define READ_TIMEOUT_MS 500
#define LOGIN_TIMEOUT_SECONDS 35
#define STREAM_TIMEOUT_SECONDS 45
#define VIDEO_HEADER_BYTES 32U
#define MAX_VIDEO_FRAME_BYTES (8U * 1024U * 1024U)
#define MIN_H264_FRAMES 3U
#define MIN_H264_BYTES 1024U

typedef void *(*client_create_fn)(const char *, const char *);
typedef int (*client_connect_fn)(void *, int, const char *, int);
typedef bool (*client_login_fn)(void *, const char *, const char *);
typedef bool (*client_write_cgi_fn)(void *, const char *, int);
typedef int (*client_read_fn)(void *, int, void *, int, int, int *);
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

static uint16_t read_le16(const unsigned char *buffer) {
    return (uint16_t)buffer[0] | ((uint16_t)buffer[1] << 8);
}

static bool read_client_exact(client_read_fn client_read, void *client, int channel,
                              unsigned char *buffer, size_t size,
                              time_t deadline) {
    size_t offset = 0;
    while (offset < size && time(NULL) <= deadline) {
        int received = 0;
        int result = client_read(client, channel, buffer + offset,
                                 (int)(size - offset), READ_TIMEOUT_MS, &received);
        if (received > 0 && (size_t)received <= size - offset) offset += (size_t)received;
        if (offset == size) return true;
        if (result != -3 && result < 0) return false;
    }
    return false;
}

static uint32_t read_le32(const unsigned char *buffer) {
    return (uint32_t)buffer[0] | ((uint32_t)buffer[1] << 8) |
           ((uint32_t)buffer[2] << 16) | ((uint32_t)buffer[3] << 24);
}

static bool is_login_response(uint16_t command) {
    return command == LOGIN_RESPONSE;
}

static bool parse_result_code(const unsigned char *payload, size_t size, int *result) {
    static const char key[] = "result";
    for (size_t i = 0; i + sizeof(key) - 1 < size; ++i) {
        if (memcmp(payload + i, key, sizeof(key) - 1) != 0) continue;
        size_t cursor = i + sizeof(key) - 1;
        while (cursor < size && (payload[cursor] == ' ' || payload[cursor] == '\t')) cursor++;
        if (cursor >= size || payload[cursor++] != '=') continue;
        while (cursor < size && (payload[cursor] == ' ' || payload[cursor] == '\t' ||
                                 payload[cursor] == '\"' || payload[cursor] == '\'')) cursor++;
        int sign = 1;
        if (cursor < size && payload[cursor] == '-') {
            sign = -1;
            cursor++;
        }
        if (cursor >= size || payload[cursor] < '0' || payload[cursor] > '9') continue;
        int value = 0;
        while (cursor < size && payload[cursor] >= '0' && payload[cursor] <= '9') {
            if (value > 100000) return false;
            value = value * 10 + payload[cursor++] - '0';
        }
        *result = sign * value;
        return true;
    }
    return false;
}

static bool await_login_response(client_read_fn client_read, void *client,
                                 uint16_t *response_command, int *result_code) {
    time_t deadline = time(NULL) + LOGIN_TIMEOUT_SECONDS;
    while (time(NULL) <= deadline) {
        unsigned char header[8];
        if (!read_client_exact(client_read, client, COMMAND_CHANNEL,
                               header, sizeof(header), deadline)) return false;
        uint16_t magic = read_le16(header);
        uint16_t command = read_le16(header + 2);
        uint16_t length = read_le16(header + 4);
        if (magic != 0x0a01U || length > MAX_COMMAND_BYTES) return false;
        unsigned char *payload = calloc((size_t)length + 1, 1);
        if (payload == NULL) return false;
        bool read_ok = length == 0 ||
            read_client_exact(client_read, client, COMMAND_CHANNEL,
                              payload, length, deadline);
        bool parsed = false;
        int code = 0;
        if (read_ok && is_login_response(command)) parsed = parse_result_code(payload, length, &code);
        memset(payload, 0, (size_t)length + 1);
        free(payload);
        if (!read_ok) return false;
        if (parsed) {
            *response_command = command;
            *result_code = code;
            return true;
        }
    }
    return false;
}

static bool inspect_h264_payload(const unsigned char *payload, size_t size,
                                 bool *keyframe_seen) {
    bool valid = false;
    for (size_t i = 0; i + 4 < size; ++i) {
        size_t nal = 0;
        if (payload[i] == 0 && payload[i + 1] == 0 && payload[i + 2] == 1) {
            nal = i + 3;
        } else if (i + 4 < size && payload[i] == 0 && payload[i + 1] == 0 &&
                   payload[i + 2] == 0 && payload[i + 3] == 1) {
            nal = i + 4;
        }
        if (nal == 0 || nal >= size) continue;
        uint8_t type = payload[nal] & 0x1fU;
        if (type >= 1U && type <= 12U) {
            valid = true;
            if (type == 5U || type == 7U) *keyframe_seen = true;
        }
    }
    return valid;
}

static bool await_h264_frames(client_read_fn client_read, void *client,
                              unsigned int *frames, unsigned long long *bytes,
                              bool *keyframe_seen, unsigned int *h265_frames) {
    time_t deadline = time(NULL) + STREAM_TIMEOUT_SECONDS;
    while (time(NULL) <= deadline &&
           (*frames < MIN_H264_FRAMES || *bytes < MIN_H264_BYTES || !*keyframe_seen)) {
        unsigned char header[VIDEO_HEADER_BYTES];
        if (!read_client_exact(client_read, client, VIDEO_CHANNEL,
                               header, sizeof(header), deadline)) return false;
        if (read_le32(header) != 0xa815aa55U) return false;
        uint32_t length = read_le32(header + 16);
        if (length == 0 || length > MAX_VIDEO_FRAME_BYTES) return false;
        unsigned char *payload = malloc(length);
        if (payload == NULL) return false;
        bool read_ok = read_client_exact(client_read, client, VIDEO_CHANNEL,
                                         payload, length, deadline);
        if (!read_ok) {
            memset(payload, 0, length);
            free(payload);
            return false;
        }
        if (header[4] == 0x10U || header[4] == 0x11U) {
            (*h265_frames)++;
        } else if (inspect_h264_payload(payload, length, keyframe_seen)) {
            (*frames)++;
            *bytes += length;
        }
        memset(payload, 0, length);
        free(payload);
    }
    return *frames >= MIN_H264_FRAMES && *bytes >= MIN_H264_BYTES && *keyframe_seen;
}

int main(int argc, char **argv) {
    bool stream_test = argc == 3 && strcmp(argv[2], "--stream-test") == 0;
    bool authenticate = stream_test ||
        (argc == 3 && strcmp(argv[2], "--authenticate") == 0);
    if (argc != 2 && !authenticate) {
        fputs("usage: okam-hybris-connect /path/to/libOKSMARTPPCS.so "
              "[--authenticate|--stream-test]\n", stderr);
        return 2;
    }
    char *uid = read_field();
    char *service_parameter = read_field();
    char *device_password = authenticate ? read_field() : NULL;
    if (uid == NULL || service_parameter == NULL ||
        (authenticate && device_password == NULL) || !initialize_stack_guard()) {
        free(uid);
        free(service_parameter);
        free(device_password);
        fputs("invalid native P2P input\n", stderr);
        return 3;
    }
    hybris_set_hook_callback(okam_hook);
    void *library = android_dlopen(argv[1], RTLD_LAZY | RTLD_LOCAL);
    if (library == NULL) {
        free(uid);
        free(service_parameter);
        if (device_password != NULL) {
            memset(device_password, 0, strlen(device_password));
            free(device_password);
        }
        fputs("official native P2P library could not be loaded\n", stderr);
        return 3;
    }

    client_create_fn client_create = (client_create_fn)android_dlsym(library, "client_create");
    client_connect_fn client_connect = (client_connect_fn)android_dlsym(library, "client_connect");
    client_login_fn client_login = (client_login_fn)android_dlsym(library, "client_login");
    client_write_cgi_fn client_write_cgi =
        (client_write_cgi_fn)android_dlsym(library, "client_write_cgi");
    client_read_fn client_read = (client_read_fn)android_dlsym(library, "client_read");
    client_disconnect_fn client_disconnect =
        (client_disconnect_fn)android_dlsym(library, "client_disconnect");
    client_destroy_fn client_destroy = (client_destroy_fn)android_dlsym(library, "client_destroy");
    if (client_create == NULL || client_connect == NULL ||
        client_disconnect == NULL || client_destroy == NULL) {
        android_dlclose(library);
        free(uid);
        free(service_parameter);
        if (device_password != NULL) {
            memset(device_password, 0, strlen(device_password));
            free(device_password);
        }
        fputs("official native P2P lifecycle API is incomplete\n", stderr);
        return 3;
    }
    if (authenticate && (client_login == NULL || client_read == NULL)) {
        android_dlclose(library);
        free(uid);
        free(service_parameter);
        memset(device_password, 0, strlen(device_password));
        free(device_password);
        fputs("official native P2P authentication API is incomplete\n", stderr);
        return 3;
    }
    if (stream_test && client_write_cgi == NULL) {
        android_dlclose(library);
        free(uid);
        free(service_parameter);
        memset(device_password, 0, strlen(device_password));
        free(device_password);
        fputs("official native P2P live-stream API is incomplete\n", stderr);
        return 3;
    }

    void *client = client_create(uid, NULL);
    memset(uid, 0, strlen(uid));
    free(uid);
    int state = -1;
    bool connected = false;
    bool disconnected = false;
    bool login_sent = false;
    bool login_response_received = false;
    bool authenticated = false;
    uint16_t login_command = 0;
    int login_result = 0;
    bool stream_start_sent = false;
    bool stream_stop_sent = false;
    bool h264_received = false;
    bool keyframe_seen = false;
    unsigned int h264_frames = 0;
    unsigned int h265_frames = 0;
    unsigned long long h264_bytes = 0;
    if (client != NULL) {
        state = client_connect(client, CONNECT_TYPE_NORMAL, service_parameter, 0);
        connected = state == CONNECT_STATE_ONLINE;
        if (connected && authenticate) {
            login_sent = client_login(client, "admin", device_password);
            if (login_sent) {
                login_response_received = await_login_response(
                    client_read, client, &login_command, &login_result);
                authenticated = login_response_received && login_result == 0;
            }
        }
        if (connected && authenticated && stream_test) {
            stream_start_sent = client_write_cgi(
                client, "livestream.cgi?streamid=10&substream=2&", 5000);
            if (stream_start_sent) {
                h264_received = await_h264_frames(
                    client_read, client, &h264_frames, &h264_bytes,
                    &keyframe_seen, &h265_frames);
                stream_stop_sent = client_write_cgi(
                    client, "livestream.cgi?streamid=16&substream=0&", 5000);
            }
        }
        if (connected) disconnected = client_disconnect(client);
        client_destroy(client);
    }
    memset(service_parameter, 0, strlen(service_parameter));
    free(service_parameter);
    if (device_password != NULL) {
        memset(device_password, 0, strlen(device_password));
        free(device_password);
    }
    if (stream_test) {
        printf("{\"connected\":%s,\"connect_state\":%d,\"login_sent\":%s,"
               "\"login_response_received\":%s,\"authenticated\":%s,"
               "\"login_command\":%u,\"login_result\":%d,"
               "\"stream_start_sent\":%s,\"stream_stop_sent\":%s,"
               "\"h264_received\":%s,\"h264_frames\":%u,\"h264_bytes\":%llu,"
               "\"keyframe_seen\":%s,\"h265_frames\":%u,\"disconnected\":%s}\n",
               connected ? "true" : "false", state, login_sent ? "true" : "false",
               login_response_received ? "true" : "false",
               authenticated ? "true" : "false", login_command, login_result,
               stream_start_sent ? "true" : "false", stream_stop_sent ? "true" : "false",
               h264_received ? "true" : "false", h264_frames, h264_bytes,
               keyframe_seen ? "true" : "false", h265_frames,
               disconnected ? "true" : "false");
    } else if (authenticate && login_response_received) {
        printf("{\"connected\":%s,\"connect_state\":%d,\"login_sent\":%s,"
               "\"login_response_received\":true,\"authenticated\":%s,"
               "\"login_command\":%u,\"login_result\":%d,\"disconnected\":%s}\n",
               connected ? "true" : "false", state, login_sent ? "true" : "false",
               authenticated ? "true" : "false", login_command, login_result,
               disconnected ? "true" : "false");
    } else if (authenticate) {
        printf("{\"connected\":%s,\"connect_state\":%d,\"login_sent\":%s,"
               "\"login_response_received\":false,\"authenticated\":false,"
               "\"login_command\":null,\"login_result\":null,\"disconnected\":%s}\n",
               connected ? "true" : "false", state, login_sent ? "true" : "false",
               disconnected ? "true" : "false");
    } else {
        printf("{\"connected\":%s,\"connect_state\":%d,\"disconnected\":%s}\n",
               connected ? "true" : "false", state, disconnected ? "true" : "false");
    }
    android_dlclose(library);
    if (!connected || !disconnected) return 4;
    if (stream_test && (!authenticated || !stream_start_sent || !stream_stop_sent ||
                        !h264_received)) return 6;
    return !authenticate || authenticated ? 0 : 5;
}
