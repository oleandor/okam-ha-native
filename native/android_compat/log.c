#include <stdarg.h>

/*
 * The inspected vendor libraries import only __android_log_print from
 * liblog.so. Suppress vendor messages because they may contain camera or
 * account identifiers; bridge-owned structured logs remain available.
 */
int __android_log_print(int priority, const char *tag, const char *format, ...) {
    (void)priority;
    (void)tag;
    (void)format;
    return 0;
}
