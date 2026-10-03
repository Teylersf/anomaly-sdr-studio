setlocal EnableDelayedExpansion
@echo on

:: Configure
set "SLN_PLAT=%CMAKE_GENERATOR_PLATFORM%"
set "SLN_TOOLSET=%CMAKE_GENERATOR_TOOLSET%"
set "SLN_FILE=msvc\libusb.sln"

set "MSBUILD_CMD=%VSINSTALLDIR%MSBuild\%VS_VERSION%\Bin\MSBuild.exe"
if not exist "%MSBUILD_CMD%" (
  :: Azure has at least VS 2017
  :: find VS 2017+ installations using vswhere tool
  echo Searching for MSBuild using vswhere...
  set "VSWHERE_CMD=C:\Program Files (x86)\Microsoft Visual Studio\Installer\vswhere.exe"
  if not exist "!VSWHERE_CMD!" (
    set "VSWHERE_CMD=vswhere"
  )
  echo vswhere path: !VSWHERE_CMD!
  for /f "usebackq tokens=*" %%i in (`"!VSWHERE_CMD!" -latest -products * -requires Microsoft.Component.MSBuild -find MSBuild\**\Bin\MSBuild.exe`) do (
    set "MSBUILD_CMD=%%i"
  )
  if not exist "!MSBUILD_CMD!" (
    :: try just finding the VS 2017 installation path
    for /f "usebackq tokens=*" %%i in (`"!VSWHERE_CMD!" -latest -products * -requires Microsoft.Component.MSBuild -version ^[15.0^,16.0^) -property installationPath`) do (
      set "vsdir=%%i"
    )
    set "MSBUILD_CMD=!vsdir!\MSBuild\15.0\Bin\MSBuild.exe"
    if not exist "!MSBUILD_CMD!" (
      :: try a sensible default
      set "MSBUILD_CMD=C:\Program Files (x86)\Microsoft Visual Studio\2017\Enterprise\MSBuild\15.0\Bin\MSBuild.exe"
      if not exist "!MSBUILD_CMD!" (
        echo Could not find MSBuild.exe
        exit 1
      )
    )
  )
  echo MSBuild path: !MSBUILD_CMD!
)

:: Build
"%MSBUILD_CMD%" "%SLN_FILE%" ^
  /p:Configuration="Release" ^
  /p:Platform="%SLN_PLAT%" ^
  /p:PlatformToolset="%SLN_TOOLSET%" ^
  /t:libusb_dll ^
  /verbosity:normal
if errorlevel 1 exit 1

:: Install
cmake -E make_directory %LIBRARY_BIN%
if errorlevel 1 exit 1
cmake -E copy %SRC_DIR%\build\%SLN_TOOLSET%\%SLN_PLAT%\Release\dll\libusb-1.0.dll %LIBRARY_BIN%\
if errorlevel 1 exit 1
cmake -E make_directory %LIBRARY_LIB%
if errorlevel 1 exit 1
cmake -E copy %SRC_DIR%\build\%SLN_TOOLSET%\%SLN_PLAT%\Release\dll\libusb-1.0.lib %LIBRARY_LIB%\
if errorlevel 1 exit 1
:: Don't include debug library in the package
rem  copy %SRC_DIR%\build\%SLN_TOOLSET%\%SLN_PLAT%\Release\dll\libusb-1.0.pdb %LIBRARY_LIB%\
rem  if errorlevel 1 exit 1
:: Don't include static library in the package
:: CFEP-18 (https://github.com/conda-forge/cfep/blob/master/cfep-18.md)
rem  copy %SRC_DIR%\build\%SLN_TOOLSET%\%SLN_PLAT%\Release\lib\libusb-1.0.lib %LIBRARY_LIB%\libusb-1.0_static.lib
rem  if errorlevel 1 exit 1
cmake -E make_directory %LIBRARY_INC%\libusb-1.0
if errorlevel 1 exit 1
cmake -E copy %SRC_DIR%\libusb\libusb.h %LIBRARY_INC%\libusb-1.0\
if errorlevel 1 exit 1

:: Create a pkg-config file
:: (use cmake just because it's convenient for replacing @VAR@ in files
cmake ^
  -DCMAKE_INSTALL_PREFIX="%LIBRARY_PREFIX%" ^
  -DCMAKE_INSTALL_LIBDIR=lib ^
  -DVERSION="%PKG_VERSION%" ^
  -P "%RECIPE_DIR%\win_make_pkgconfig.cmake"
if errorlevel 1 exit 1
