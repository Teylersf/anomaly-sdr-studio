HOST=x86_64-w64-mingw32

if [[ "$target_platform" == "win-64" ]]; then
  INSTALL_PREFIX=${PREFIX}/Library/${HOST}/sysroot/usr
else
  INSTALL_PREFIX=${PREFIX}/${HOST}/sysroot/usr
fi

pushd mingw-w64-headers
  touch include/windows.*.h include/wincrypt.h include/prsht.h
popd

./configure \
  --host=${HOST} \
  --disable-lib32 \
  --enable-lib64 \
  --enable-sdk=all \
  --with-default-win32-winnt=0x603 \
  --with-default-msvcrt=ucrt \
  --prefix=${INSTALL_PREFIX} \
  --enable-wildcard \
  --disable-dependency-tracking \
  --enable-idl \
  --without-widl \
  --with-libraries=winpthreads

make -j${CPU_COUNT}

${HOST}-ar rcs libssp.a
${HOST}-ar rcs libssp_nonshared.a
