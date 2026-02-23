cmake_minimum_required(VERSION 3.20)
project(convertCIO LANGUAGES CXX)

set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)

# For MSVC: statically link the C/C++ runtime library
if(MSVC)
    set(CMAKE_MSVC_RUNTIME_LIBRARY "MultiThreaded$<$<CONFIG:Debug>:Debug>")
endif()

add_executable(convertCIO src/convertCIO.cpp)

# For GCC/MinGW: use -static flag
if(NOT MSVC)
    target_link_options(convertCIO PRIVATE -static)
endif()
