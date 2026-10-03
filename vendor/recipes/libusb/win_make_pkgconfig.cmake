# cmake script to create a pkg-config file for the Windows package
# VERSION variable should be set when calling this
cmake_minimum_required(VERSION 3.18)

if(NOT DEFINED VERSION)
    message(FATAL_ERROR "Must define VERSION variable")
endif()

file(TO_CMAKE_PATH "${CMAKE_INSTALL_PREFIX}" CMAKE_INSTALL_PREFIX)

include(GNUInstallDirs)

set(prefix "${CMAKE_INSTALL_PREFIX}")
set(exec_prefix "\${prefix}")
set(libdir "\${prefix}/${CMAKE_INSTALL_LIBDIR}")
set(includedir "\${prefix}/${CMAKE_INSTALL_INCLUDEDIR}")

set(PC_CONTENT [=[
prefix=@prefix@
exec_prefix=@exec_prefix@
libdir=@libdir@
includedir=@includedir@/libusb-1.0

Name: libusb-1.0
Description: C API for USB device access from Linux, Mac OS X, Windows, OpenBSD/NetBSD and Solaris userspace
Version: @VERSION@
Libs: -L${libdir} -llibusb-1.0
Cflags: -I${includedir}
]=])

file(CONFIGURE OUTPUT ${CMAKE_INSTALL_FULL_LIBDIR}/pkgconfig/libusb-1.0.pc
    CONTENT ${PC_CONTENT}
    @ONLY
    NEWLINE_STYLE UNIX
)
