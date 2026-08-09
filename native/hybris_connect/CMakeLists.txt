cmake_minimum_required(VERSION 3.16)
project(okam_hybris_connect C)

set(CMAKE_C_STANDARD 11)
set(CMAKE_C_STANDARD_REQUIRED ON)
add_executable(okam-hybris-connect hybris_connect.c)
target_include_directories(okam-hybris-connect PRIVATE ${HYBRIS_INCLUDE_DIR})
target_link_libraries(okam-hybris-connect PRIVATE ${HYBRIS_COMMON_LIBRARY} dl)
target_compile_options(okam-hybris-connect PRIVATE -Wall -Wextra -Werror)
