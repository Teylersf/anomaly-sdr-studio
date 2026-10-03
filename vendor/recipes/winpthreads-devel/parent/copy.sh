# Temporary hacks. Remove after bootstrapping is done

HOST=x86_64-w64-mingw32
export PATH=$SRC_DIR/cf-compilers/bin:$PATH

if [[ "$target_platform" != "win-64" ]]; then
  mkdir -p ${PREFIX}/${HOST}/sysroot
  ln -sf ${PREFIX}/${HOST}/sysroot/usr ${PREFIX}/${HOST}/sysroot/ucrt64
fi
