cmake_minimum_required(VERSION 3.20)
project(convertCIO LANGUAGES CXX)

set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)

add_executable(convertCIO src/convertCIO.cpp)

target_link_options(convertCIO PRIVATE -static)
