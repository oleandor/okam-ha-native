#include <arpa/inet.h>
#include <dlfcn.h>
#include <errno.h>
#include <signal.h>
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

/* Why live forwarding stopped; reported in the final sanitized summary. */
#define STREAM_END_NONE 0U
#define STREAM_END_HEADER_READ 1U
#define STREAM_END_BAD_MAGIC 2U
#define STREAM_END_BAD_LENGTH 3U
#define STREAM_END_NO_MEMORY 4U
#define STREAM_END_PAYLOAD_READ 5U
#define STREAM_END_OUTPUT_CLOSED 6U
#define STREAM_END_STOPPED 7U

typedef void *(*client_create_fn)(const char *, const char *);
typedef int (*client_connect_fn)(void *, int, const char *, int);
typedef bool (*client_login_fn)(void *, const char *, const char *);
typedef bool (*client_write_cgi_fn)(void *, const char *, int);
typedef int (*client_setvuid_fn)(void *, const char *);
typedef int (*client_read_fn)(void *, int, void *, int, int, int *);
typedef bool (*client_disconnect_fn)(void *);
typedef void (*client_destroy_fn)(void *);

static uintptr_t stack_guard;
static volatile sig_atomic_t stream_running = 1;
extern void __stack_chk_fail(void);

static void stop_streaming(int signal_number) {
    (void)signal_number;
    stream_running = 0;
}

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

/* Integer value of `key=` in a CGI response, matched only at a key boundary. */
static bool parse_int_key(const unsigned char *payload, size_t size, const char *key,
                          int *value_out) {
    size_t key_size = strlen(key);
    for (size_t i = 0; i + key_size < size; ++i) {
        if (memcmp(payload + i, key, key_size) != 0) continue;
        if (i > 0) {
            unsigned char before = payload[i - 1];
            if ((before >= 'a' && before <= 'z') || (before >= 'A' && before <= 'Z') ||
                (before >= '0' && before <= '9') || before == '_') continue;
        }
        size_t cursor = i + key_size;
        while (cursor < size && (payload[cursor] == ' ' || payload[cursor] == '\t')) cursor++;
        if (cursor >= size || payload[cursor++] != '=') continue;
        while (cursor < size && (payload[cursor] == ' ' || payload[cursor] == '\"' ||
                                 payload[cursor] == '\'')) cursor++;
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
        *value_out = sign * value;
        return true;
    }
    return false;
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

static bool write_stdout(const unsigned char *payload, size_t size) {
    size_t offset = 0;
    while (offset < size) {
        ssize_t written = write(STDOUT_FILENO, payload + offset, size - offset);
        if (written > 0) {
            offset += (size_t)written;
            continue;
        }
        if (written < 0 && errno == EINTR) continue;
        return false;
    }
    return true;
}

/* Sent by the official O-KAM Pro PC client right before livestream.cgi. It is
 * a board/capability query that some multi-sensor cameras are believed to
 * need before they send video. Its reply is ignored. Opt-in, see below. */
#define PRE_LIVE_COMMAND "trans_cmd_string.cgi?cmd=8000&command=0&"
/* Keep-alive the PC client sends to every camera every few seconds. */
#define ACTIVE_TIME_COMMAND "trans_cmd_string.cgi?cmd=2131&command=1&DevActiveTime=10&"

/* `userId=<account id>&`, appended to every command like the official PC
 * client does. Empty when no account id was supplied. */
static char user_id_suffix[48];

/* The PC-client live start (keep-alive + pre-live query before livestream.cgi,
 * userId on commands, VUID registration) is opt-in. The bridge only sends the
 * optional fourth stdin field when the user enabled it; without that field the
 * helper sends exactly the original single livestream.cgi command. */
static bool pc_client_live_start = false;

static void set_account_user_id(const char *user_id) {
    user_id_suffix[0] = 0;
    if (user_id == NULL) return;
    size_t size = strlen(user_id);
    if (size == 0 || size > 20) return;
    for (size_t i = 0; i < size; ++i) {
        if (user_id[i] < '0' || user_id[i] > '9') return;
    }
    snprintf(user_id_suffix, sizeof(user_id_suffix), "userId=%s&", user_id);
}

static bool send_cgi(client_write_cgi_fn write_cgi, void *client, const char *command) {
    if (user_id_suffix[0] == 0) return write_cgi(client, command, 5000);
    char full[256];
    int written = snprintf(full, sizeof(full), "%s%s", command, user_id_suffix);
    if (written < 0 || (size_t)written >= sizeof(full)) return write_cgi(client, command, 5000);
    bool ok = write_cgi(client, full, 5000);
    memset(full, 0, sizeof(full));
    return ok;
}

static void send_pre_live_commands(client_write_cgi_fn write_cgi, void *client) {
    if (!pc_client_live_start) return;
    send_cgi(write_cgi, client, ACTIVE_TIME_COMMAND);
    send_cgi(write_cgi, client, PRE_LIVE_COMMAND);
}

#define RAW_CHUNK_BYTES (64U * 1024U)

static bool starts_with_annex_b(const unsigned char *data) {
    return (data[0] == 0 && data[1] == 0 && data[2] == 0 && data[3] == 1) ||
           (data[0] == 0 && data[1] == 0 && data[2] == 1);
}

/* Some cameras (seen on a multi-sensor model) send a bare Annex-B elementary
 * stream on the video channel, without the 32-byte frame header. Forward it
 * unchanged; the bridge identifies the codec itself. */
static bool forward_raw_stream(client_read_fn client_read, void *client,
                               const unsigned char *first, size_t first_size,
                               unsigned int *frames, unsigned long long *bytes,
                               unsigned int *end_reason) {
    if (!write_stdout(first, first_size)) {
        *end_reason = STREAM_END_OUTPUT_CLOSED;
        return true;
    }
    *bytes += first_size;
    (*frames)++;
    unsigned char *chunk = malloc(RAW_CHUNK_BYTES);
    if (chunk == NULL) {
        *end_reason = STREAM_END_NO_MEMORY;
        return true;
    }
    time_t deadline = time(NULL) + STREAM_TIMEOUT_SECONDS;
    while (stream_running) {
        int received = 0;
        int result = client_read(client, VIDEO_CHANNEL, chunk, (int)RAW_CHUNK_BYTES,
                                 READ_TIMEOUT_MS, &received);
        if (received > 0 && (size_t)received <= RAW_CHUNK_BYTES) {
            deadline = time(NULL) + STREAM_TIMEOUT_SECONDS;
            *bytes += (unsigned long long)received;
            if (received >= 4 && starts_with_annex_b(chunk)) (*frames)++;
            bool ok = write_stdout(chunk, (size_t)received);
            memset(chunk, 0, (size_t)received);
            if (!ok) {
                *end_reason = STREAM_END_OUTPUT_CLOSED;
                stream_running = 0;
                break;
            }
            continue;
        }
        if ((result != -3 && result < 0) || time(NULL) > deadline) {
            *end_reason = STREAM_END_HEADER_READ;
            break;
        }
    }
    free(chunk);
    return true;
}

static bool is_h265_frame(const unsigned char *header) {
    return header[4] == 0x10U || header[4] == 0x11U;
}

/* Forward live video to stdout as Annex-B. H.264 frames are validated before
 * forwarding; H.265 frames (flagged in the frame header) are forwarded as
 * they are, so cameras that only encode H.265 still produce a stream. The
 * bridge detects the codec from the elementary stream itself. */
static bool forward_video_frames(client_read_fn client_read, void *client,
                                 unsigned int *frames, unsigned long long *bytes,
                                 bool *keyframe_seen, unsigned int *h265_frames,
                                 unsigned int *end_reason, uint32_t *last_magic,
                                 unsigned int *last_frame_type) {
    *end_reason = STREAM_END_STOPPED;
    while (stream_running) {
        time_t deadline = time(NULL) + STREAM_TIMEOUT_SECONDS;
        unsigned char header[VIDEO_HEADER_BYTES];
        if (!read_client_exact(client_read, client, VIDEO_CHANNEL,
                               header, sizeof(header), deadline)) {
            *end_reason = STREAM_END_HEADER_READ;
            return false;
        }
        *last_magic = read_le32(header);
        *last_frame_type = header[4];
        if (*last_magic != 0xa815aa55U) {
            if (*frames == 0 && *h265_frames == 0 && starts_with_annex_b(header)) {
                return forward_raw_stream(client_read, client, header, sizeof(header),
                                          frames, bytes, end_reason);
            }
            *end_reason = STREAM_END_BAD_MAGIC;
            return false;
        }
        uint32_t length = read_le32(header + 16);
        if (length == 0 || length > MAX_VIDEO_FRAME_BYTES) {
            *end_reason = STREAM_END_BAD_LENGTH;
            return false;
        }
        unsigned char *payload = malloc(length);
        if (payload == NULL) {
            *end_reason = STREAM_END_NO_MEMORY;
            return false;
        }
        bool read_ok = read_client_exact(client_read, client, VIDEO_CHANNEL,
                                         payload, length, deadline);
        bool write_ok = true;
        if (read_ok) {
            if (is_h265_frame(header)) {
                (*h265_frames)++;
                *bytes += length;
                write_ok = write_stdout(payload, length);
            } else if (inspect_h264_payload(payload, length, keyframe_seen)) {
                (*frames)++;
                *bytes += length;
                write_ok = write_stdout(payload, length);
            }
        }
        memset(payload, 0, length);
        free(payload);
        if (!read_ok) {
            *end_reason = STREAM_END_PAYLOAD_READ;
            return false;
        }
        if (!write_ok) {
            *end_reason = STREAM_END_OUTPUT_CLOSED;
            stream_running = 0;
            break;
        }
    }
    return *frames > 0 || *h265_frames > 0;
}

#define PROBE_CHANNELS 8
#define PROBE_SECONDS 6
#define PROBE_READ_TIMEOUT_MS 20
#define PROBE_BUFFER_BYTES (64U * 1024U)

/* Values the official app sends: VideoResolution index (high=1, general=2,
 * unknown=3, low=4), 100 for superHD; 0, 5 and 6 cover the remaining enum. */
static const int probe_substreams[] = {2, 1, 0, 4, 3, 100, 5, 6};
#define PROBE_SUBSTREAM_COUNT (sizeof(probe_substreams) / sizeof(probe_substreams[0]))

/* Multi-channel devices (NVRs and, it appears, multi-lens cameras) are
 * started with a JSON command through client_write_cgi_nvr instead of
 * livestream.cgi: {"live_start":{"chns":<channel bitmask>,"streammask":<n>}}.
 * Taken from the official app's startNvrStream. */
static const int probe_nvr_chns[] = {1, 1, 7, 7, 15};
static const int probe_nvr_masks[] = {0, 1, 0, 1, 0};
#define PROBE_NVR_COUNT (sizeof(probe_nvr_chns) / sizeof(probe_nvr_chns[0]))

typedef struct {
    unsigned long long bytes[PROBE_CHANNELS];
    uint32_t first_magic[PROBE_CHANNELS];
    unsigned int first_type[PROBE_CHANNELS];
    bool start_sent;
    unsigned int responses;
    unsigned int last_command;
    int last_result;
    bool last_result_seen;
} channel_probe_result;

/* Capability flags the official app reads from get_status.cgi to choose how a
 * multi-sensor camera is played. Only these integer flags are reported. */
static const char *const probe_status_keys[] = {
    "support_mutil_sensor_stream", "dev_sensor_num", "support_binocular_mutil",
    "splitScreen", "support_dual_sensor_stitch", "support_three_sensor_binocular",
    "sensornum", "binocular_num", "support_binocular", "support_fix_sensor",
    "pininpic_sensor",
};
#define PROBE_STATUS_KEY_COUNT (sizeof(probe_status_keys) / sizeof(probe_status_keys[0]))

typedef struct {
    bool requested;
    unsigned int responses;
    bool seen[PROBE_STATUS_KEY_COUNT];
    int values[PROBE_STATUS_KEY_COUNT];
} status_probe_result;

/* Read one command-channel frame if one is waiting. Returns false when no
 * frame header arrived within a short poll. */
static bool read_command_frame(client_read_fn client_read, void *client,
                               unsigned char *payload, size_t capacity,
                               unsigned int *command, size_t *length) {
    unsigned char header[8];
    int received = 0;
    client_read(client, COMMAND_CHANNEL, header, (int)sizeof(header),
                PROBE_READ_TIMEOUT_MS, &received);
    if (received <= 0 || (size_t)received > sizeof(header)) return false;
    time_t deadline = time(NULL) + 2;
    if ((size_t)received < sizeof(header) &&
        !read_client_exact(client_read, client, COMMAND_CHANNEL, header + received,
                           sizeof(header) - (size_t)received, deadline)) return false;
    uint16_t magic = read_le16(header);
    uint16_t frame_length = read_le16(header + 4);
    if (magic != 0x0a01U || frame_length >= capacity) return false;
    if (frame_length > 0 &&
        !read_client_exact(client_read, client, COMMAND_CHANNEL, payload, frame_length,
                           deadline)) return false;
    payload[frame_length] = 0;
    *command = read_le16(header + 2);
    *length = frame_length;
    return true;
}

static void note_command_response(channel_probe_result *result,
                                  const unsigned char *payload, size_t length,
                                  unsigned int command) {
    if (result == NULL) return;
    result->responses++;
    result->last_command = command;
    int code = 0;
    if (parse_int_key(payload, length, "result", &code)) {
        result->last_result = code;
        result->last_result_seen = true;
    }
}

static void probe_status(client_read_fn client_read, client_write_cgi_fn client_write_cgi,
                         void *client, status_probe_result *status) {
    unsigned char *payload = malloc(MAX_COMMAND_BYTES);
    if (payload == NULL) return;
    status->requested = send_cgi(client_write_cgi, client, "get_status.cgi?");
    time_t deadline = time(NULL) + PROBE_SECONDS;
    while (status->requested && time(NULL) < deadline) {
        unsigned int command = 0;
        size_t length = 0;
        if (!read_command_frame(client_read, client, payload, MAX_COMMAND_BYTES,
                                &command, &length)) continue;
        status->responses++;
        for (size_t key = 0; key < PROBE_STATUS_KEY_COUNT; ++key) {
            int value = 0;
            if (parse_int_key(payload, length, probe_status_keys[key], &value)) {
                status->seen[key] = true;
                status->values[key] = value;
            }
        }
        memset(payload, 0, length);
    }
    free(payload);
}

/* Drain every channel for a while, counting bytes and remembering only the
 * first frame header word and type byte per channel. No payload is kept. */
static void drain_channels(client_read_fn client_read, void *client,
                           unsigned char *buffer, int seconds,
                           channel_probe_result *result) {
    time_t deadline = time(NULL) + seconds;
    while (time(NULL) < deadline) {
        unsigned int command = 0;
        size_t length = 0;
        if (read_command_frame(client_read, client, buffer, PROBE_BUFFER_BYTES,
                               &command, &length)) {
            note_command_response(result, buffer, length, command);
            memset(buffer, 0, length);
        }
        for (int channel = 1; channel < PROBE_CHANNELS; ++channel) {
            int received = 0;
            client_read(client, channel, buffer, (int)PROBE_BUFFER_BYTES,
                        PROBE_READ_TIMEOUT_MS, &received);
            if (received <= 0 || (size_t)received > PROBE_BUFFER_BYTES) continue;
            if (result != NULL) {
                if (result->bytes[channel] == 0 && received >= 5) {
                    result->first_magic[channel] = read_le32(buffer);
                    result->first_type[channel] = buffer[4];
                }
                result->bytes[channel] += (unsigned long long)received;
            }
            memset(buffer, 0, (size_t)received);
        }
    }
}

/* Try each known live substream and report which channels carry data. Used to
 * learn how multi-sensor cameras deliver video. */
static void probe_live_channels(client_read_fn client_read, client_write_cgi_fn client_write_cgi,
                                void *client, channel_probe_result *results) {
    unsigned char *buffer = malloc(PROBE_BUFFER_BYTES);
    if (buffer == NULL) return;
    send_pre_live_commands(client_write_cgi, client);
    drain_channels(client_read, client, buffer, 1, NULL);
    for (size_t index = 0; index < PROBE_SUBSTREAM_COUNT; ++index) {
        char command[96];
        snprintf(command, sizeof(command), "livestream.cgi?streamid=10&substream=%d&",
                 probe_substreams[index]);
        results[index].start_sent = send_cgi(client_write_cgi, client, command);
        if (results[index].start_sent) {
            drain_channels(client_read, client, buffer, PROBE_SECONDS, &results[index]);
        }
        send_cgi(client_write_cgi, client, "livestream.cgi?streamid=16&substream=0&");
        drain_channels(client_read, client, buffer, 1, NULL);
    }
    free(buffer);
}

static void probe_nvr_live_channels(client_read_fn client_read,
                                    client_write_cgi_fn client_write_cgi_nvr, void *client,
                                    channel_probe_result *results) {
    unsigned char *buffer = malloc(PROBE_BUFFER_BYTES);
    if (buffer == NULL) return;
    for (size_t index = 0; index < PROBE_NVR_COUNT; ++index) {
        char command[96];
        snprintf(command, sizeof(command),
                 "{\"live_start\":{\"chns\":%d,\"streammask\":%d}}",
                 probe_nvr_chns[index], probe_nvr_masks[index]);
        results[index].start_sent = client_write_cgi_nvr(client, command, 5000);
        if (results[index].start_sent) {
            drain_channels(client_read, client, buffer, PROBE_SECONDS, &results[index]);
        }
        client_write_cgi_nvr(client, "{\"live_stop\":{}}", 5000);
        drain_channels(client_read, client, buffer, 1, NULL);
    }
    free(buffer);
}

static void print_probe_responses(const channel_probe_result *result) {
    printf(",\"responses\":%u,\"last_command\":%u,\"last_result\":",
           result->responses, result->last_command);
    if (result->last_result_seen) printf("%d", result->last_result);
    else printf("null");
}

static void print_probe_channels(const channel_probe_result *result) {
    bool first = true;
    for (int channel = 1; channel < PROBE_CHANNELS; ++channel) {
        if (result->bytes[channel] == 0) continue;
        printf("%s{\"channel\":%d,\"bytes\":%llu,\"magic\":%u,\"type\":%u}",
               first ? "" : ",", channel, result->bytes[channel],
               (unsigned int)result->first_magic[channel], result->first_type[channel]);
        first = false;
    }
}

static void print_nvr_probe(const channel_probe_result *results, bool available) {
    printf(",\"nvr_probes\":[");
    for (size_t index = 0; available && index < PROBE_NVR_COUNT; ++index) {
        printf("%s{\"chns\":%d,\"streammask\":%d,\"start_sent\":%s",
               index ? "," : "", probe_nvr_chns[index], probe_nvr_masks[index],
               results[index].start_sent ? "true" : "false");
        print_probe_responses(&results[index]);
        printf(",\"channels\":[");
        print_probe_channels(&results[index]);
        printf("]}");
    }
    printf("]");
}


static void print_channel_probe(const channel_probe_result *results) {
    printf(",\"probes\":[");
    for (size_t index = 0; index < PROBE_SUBSTREAM_COUNT; ++index) {
        printf("%s{\"substream\":%d,\"start_sent\":%s",
               index ? "," : "", probe_substreams[index],
               results[index].start_sent ? "true" : "false");
        print_probe_responses(&results[index]);
        printf(",\"channels\":[");
        print_probe_channels(&results[index]);
        printf("]}");
    }
    printf("]");
}

static void print_status_probe(const status_probe_result *status) {
    printf(",\"status\":{\"requested\":%s,\"responses\":%u,\"flags\":{",
           status->requested ? "true" : "false", status->responses);
    bool first = true;
    for (size_t key = 0; key < PROBE_STATUS_KEY_COUNT; ++key) {
        if (!status->seen[key]) continue;
        printf("%s\"%s\":%d", first ? "" : ",", probe_status_keys[key], status->values[key]);
        first = false;
    }
    printf("}}");
}

int main(int argc, char **argv) {
    bool channel_probe = argc == 3 && strcmp(argv[2], "--channel-probe") == 0;
    bool stream_test = argc == 3 && strcmp(argv[2], "--stream-test") == 0;
    bool stream_stdout = argc == 3 && strcmp(argv[2], "--stream-stdout") == 0;
    bool live_mode = stream_test || stream_stdout;
    bool authenticate = live_mode || channel_probe ||
        (argc == 3 && strcmp(argv[2], "--authenticate") == 0);
    if (argc != 2 && !authenticate) {
        fputs("usage: okam-hybris-connect /path/to/libOKSMARTPPCS.so "
              "[--authenticate|--stream-test|--stream-stdout|--channel-probe]\n", stderr);
        return 2;
    }
    char *uid = read_field();
    char *service_parameter = read_field();
    char *device_password = authenticate ? read_field() : NULL;
    /* Optional fourth field: the account's numeric user id. */
    char *account_user_id = authenticate && device_password != NULL ? read_field() : NULL;
    pc_client_live_start = account_user_id != NULL;
    set_account_user_id(account_user_id);
    /* Optional fifth field: the virtual device id (VUID) the account lists for
     * this camera when the transport id differs. The official app registers it
     * on the client right after client_create; VUID cameras answer commands
     * without it but send no video. */
    char *virtual_uid = account_user_id != NULL ? read_field() : NULL;
    free(account_user_id);
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
    if ((live_mode || channel_probe) && client_write_cgi == NULL) {
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
    if (client != NULL && virtual_uid != NULL) {
        client_setvuid_fn client_setvuid =
            (client_setvuid_fn)android_dlsym(library, "client_setvuid");
        if (client_setvuid != NULL) client_setvuid(client, virtual_uid);
    }
    if (virtual_uid != NULL) {
        memset(virtual_uid, 0, strlen(virtual_uid));
        free(virtual_uid);
        virtual_uid = NULL;
    }
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
    unsigned int stream_end_reason = STREAM_END_NONE;
    uint32_t last_magic = 0;
    unsigned int last_frame_type = 0;
    channel_probe_result probe_results[PROBE_SUBSTREAM_COUNT];
    memset(probe_results, 0, sizeof(probe_results));
    channel_probe_result nvr_results[PROBE_NVR_COUNT];
    memset(nvr_results, 0, sizeof(nvr_results));
    status_probe_result status_result;
    memset(&status_result, 0, sizeof(status_result));
    client_write_cgi_fn client_write_cgi_nvr =
        (client_write_cgi_fn)android_dlsym(library, "client_write_cgi_nvr");
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
        if (connected && authenticated && channel_probe) {
            probe_status(client_read, client_write_cgi, client, &status_result);
            probe_live_channels(client_read, client_write_cgi, client, probe_results);
            if (client_write_cgi_nvr != NULL) {
                probe_nvr_live_channels(client_read, client_write_cgi_nvr, client, nvr_results);
            }
        }
        if (connected && authenticated && live_mode) {
            send_pre_live_commands(client_write_cgi, client);
            stream_start_sent = send_cgi(client_write_cgi, client,
                                         "livestream.cgi?streamid=10&substream=2&");
            if (stream_start_sent) {
                if (stream_stdout) {
                    signal(SIGPIPE, SIG_IGN);
                    signal(SIGINT, stop_streaming);
                    signal(SIGTERM, stop_streaming);
                    h264_received = forward_video_frames(
                        client_read, client, &h264_frames, &h264_bytes,
                        &keyframe_seen, &h265_frames, &stream_end_reason,
                        &last_magic, &last_frame_type);
                } else {
                    h264_received = await_h264_frames(
                        client_read, client, &h264_frames, &h264_bytes,
                        &keyframe_seen, &h265_frames);
                }
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
    if (stream_stdout) {
        fprintf(stderr,
                "{\"connected\":%s,\"connect_state\":%d,\"login_sent\":%s,"
                "\"login_response_received\":%s,\"authenticated\":%s,"
                "\"login_command\":%u,\"login_result\":%d,"
                "\"stream_start_sent\":%s,\"stream_stop_sent\":%s,"
                "\"h264_received\":%s,\"h264_frames\":%u,\"h264_bytes\":%llu,"
                "\"keyframe_seen\":%s,\"h265_frames\":%u,"
                "\"stream_end_reason\":%u,\"last_magic\":%u,"
                "\"last_frame_type\":%u,\"disconnected\":%s}\n",
                connected ? "true" : "false", state, login_sent ? "true" : "false",
                login_response_received ? "true" : "false",
                authenticated ? "true" : "false", login_command, login_result,
                stream_start_sent ? "true" : "false", stream_stop_sent ? "true" : "false",
                h264_received ? "true" : "false", h264_frames, h264_bytes,
                keyframe_seen ? "true" : "false", h265_frames,
                stream_end_reason, (unsigned int)last_magic, last_frame_type,
                disconnected ? "true" : "false");
    } else if (stream_test) {
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
    } else if (channel_probe) {
        printf("{\"connected\":%s,\"connect_state\":%d,\"login_sent\":%s,"
               "\"login_response_received\":%s,\"authenticated\":%s,"
               "\"login_result\":%d",
               connected ? "true" : "false", state, login_sent ? "true" : "false",
               login_response_received ? "true" : "false",
               authenticated ? "true" : "false", login_result);
        if (authenticated) {
            print_status_probe(&status_result);
            print_channel_probe(probe_results);
            print_nvr_probe(nvr_results, client_write_cgi_nvr != NULL);
        }
        printf(",\"disconnected\":%s}\n", disconnected ? "true" : "false");
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
    if (live_mode && (!authenticated || !stream_start_sent || !stream_stop_sent ||
                      !h264_received)) return 6;
    return !authenticate || authenticated ? 0 : 5;
}
