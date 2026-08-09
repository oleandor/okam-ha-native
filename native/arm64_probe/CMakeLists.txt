cmake_minimum_required(VERSION 3.20)
project(okam_arm64_probe C)

add_executable(okam-arm64-probe loader_probe.c)
target_link_libraries(okam-arm64-probe PRIVATE dl)
