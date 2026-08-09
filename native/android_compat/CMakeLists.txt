cmake_minimum_required(VERSION 3.20)
project(okam_android_compat C)

add_library(android_compat_android SHARED empty.c)
set_target_properties(android_compat_android PROPERTIES
    OUTPUT_NAME android
    PREFIX "lib"
    SUFFIX ".so"
)

add_library(android_compat_log SHARED log.c)
set_target_properties(android_compat_log PROPERTIES
    OUTPUT_NAME log
    PREFIX "lib"
    SUFFIX ".so"
)

install(TARGETS
    android_compat_android
    android_compat_log
    LIBRARY DESTINATION lib
)
