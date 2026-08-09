cmake_minimum_required(VERSION 3.20)
project(okam_hybris_probe C)

find_path(HYBRIS_INCLUDE_DIR hybris/common/binding.h REQUIRED)
find_library(HYBRIS_COMMON_LIBRARY hybris-common REQUIRED)

add_executable(okam-hybris-probe hybris_probe.c)
target_include_directories(okam-hybris-probe PRIVATE "${HYBRIS_INCLUDE_DIR}")
target_link_libraries(okam-hybris-probe PRIVATE "${HYBRIS_COMMON_LIBRARY}" dl)
target_compile_options(okam-hybris-probe PRIVATE -Wall -Wextra -Werror)
