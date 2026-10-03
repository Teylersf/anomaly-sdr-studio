# Python incorporated software

The release pins Python 3.13.7 x64. `LICENSE.txt` is the complete installed Windows runtime license and its bundled notices, including bzip2, libffi, Apache terms, historical Python licenses, and Windows binary restrictions.

`INCORPORATED-SOFTWARE-LICENSES.rst` is the unmodified Python 3.13.7 documentation source containing additional incorporated-software notices, including Expat, zlib, libmpdec, mimalloc, asyncio portions, and hash implementations. Its relative documentation references do not change the included license texts.

The separately supplied `licenses/openssl`, `licenses/expat`, and `licenses/hacl` folders preserve additional upstream notices. These dependencies retain their original terms; the application MIT license does not relicense them.

Sources:

- [Python 3.13.7 license documentation source](https://github.com/python/cpython/blob/v3.13.7/Doc/license.rst)
- [Python 3.13.7 Windows dependency versions](https://github.com/python/cpython/blob/v3.13.7/PCbuild/get_externals.bat)

The packager copies `LICENSE.txt` from the runtime actually used for the build. Preserve the full text and the incorporated-software notices when redistributing the runtime.
