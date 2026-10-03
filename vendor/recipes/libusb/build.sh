#!/usr/bin/env bash
# Get an updated config.sub and config.guess
cp $BUILD_PREFIX/share/gnuconfig/config.* .

./configure --prefix=${PREFIX} --disable-static
make
make install
