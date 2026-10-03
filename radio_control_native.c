/* One-shot, selected-device control. No receive/transmit API is loaded. */
#define WIN32_LEAN_AND_MEAN
#include <windows.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>
#include <wchar.h>
#include <stdarg.h>
#include <ctype.h>

#define PATH_CAPACITY 4096
#define ERROR_CAPACITY 4096

/* Board IDs from the official v2024.02.1 hackrf_board_id enum. The original
   HackRF One and R9 are distinct board types; ID 1 identifies Jawbreaker. */
#define BOARD_ID_HACKRF1_OG 2
#define BOARD_ID_HACKRF1_R9 4

typedef struct hackrf_device hackrf_device;
/* Public hackrf_device_list_t layout from host API v2024.02.1 hackrf.h. */
typedef struct {
    char **serial_numbers;
    int *usb_board_ids;
    int *usb_device_index;
    int devicecount;
    void **usb_devices;
    int usb_devicecount;
} device_list;
typedef int (__cdecl *simple_fn)(void);
typedef device_list *(__cdecl *list_fn)(void);
typedef void (__cdecl *free_list_fn)(device_list *);
typedef int (__cdecl *open_fn)(const char *, hackrf_device **);
typedef int (__cdecl *close_fn)(hackrf_device *);
typedef int (__cdecl *version_fn)(hackrf_device *, char *, uint8_t);
typedef int (__cdecl *board_id_fn)(hackrf_device *, uint8_t *);
typedef const char *(__cdecl *error_name_fn)(int);

typedef struct {
    simple_fn init;
    simple_fn exit;
    open_fn open;
    close_fn close;
    version_fn version;
    board_id_fn board_id;
    error_name_fn error_name;
    list_fn list;
    free_list_fn free_list;
} radio_api;

static void phase(const char *state, const char *operation)
{
    fprintf(stderr, "%s:%s\n", state, operation);
    fflush(stderr);
}

static void append_error(char *errors, const char *format, ...)
{
    size_t used = strlen(errors);
    va_list arguments;
    if (used && used < ERROR_CAPACITY - 3) {
        memcpy(errors + used, "; ", 3);
        used += 2;
    }
    if (used >= ERROR_CAPACITY - 1)
        return;
    va_start(arguments, format);
    vsnprintf(errors + used, ERROR_CAPACITY - used, format, arguments);
    va_end(arguments);
    errors[ERROR_CAPACITY - 1] = '\0';
}

static int checked_call(const radio_api *api, char *errors,
                        const char *operation, int result)
{
    const char *detail;
    if (result == 0) {
        phase("done", operation);
        return 1;
    }
    /* Preserve the failing phase/code even if error-name lookup faults. */
    fprintf(stderr, "failed:%s:%d\n", operation, result);
    fflush(stderr);
    detail = api->error_name(result);
    append_error(errors, "%s: %s (%d)", operation,
                 detail ? detail : "unknown error", result);
    return 0;
}

static void json_string(const char *value)
{
    const unsigned char *cursor = (const unsigned char *)value;
    putchar('"');
    while (*cursor) {
        unsigned char byte = *cursor++;
        if (byte == '"' || byte == '\\') {
            putchar('\\');
            putchar(byte);
        } else if (byte < 0x20 || byte >= 0x7f) {
            /* Hardware text is ASCII; escape unexpected bytes deterministically. */
            printf("\\u%04x", (unsigned int)byte);
        } else {
            putchar(byte);
        }
    }
    putchar('"');
}

static void usage(FILE *destination)
{
    fputs("Usage: radio_control_native.exe --dll <path> [--serial <32 hex digits>] [--idle]\n"
          "Default: select exactly one connected HackRF and read firmware.\n"
          "--serial: select that device explicitly; never select an arbitrary device.\n"
          "--idle: checked selected-device open/close/exit only (requires --serial).\n"
          "No receive or transmit operation is started.\n", destination);
    fflush(destination);
}

static HMODULE load_radio(const wchar_t *dll_path, char *errors)
{
    wchar_t resolved[PATH_CAPACITY];
    DWORD length;
    HMODULE module;

    phase("begin", "load");
    length = GetFullPathNameW(dll_path, PATH_CAPACITY, resolved, NULL);
    if (length == 0 || length >= PATH_CAPACITY) {
        append_error(errors, "DLL path resolution failed (Win32 %lu)", GetLastError());
        return NULL;
    }
    module = LoadLibraryExW(resolved, NULL,
                           LOAD_LIBRARY_SEARCH_DLL_LOAD_DIR |
                           LOAD_LIBRARY_SEARCH_DEFAULT_DIRS);
    if (module == NULL) {
        append_error(errors, "DLL load failed (Win32 %lu)", GetLastError());
        return NULL;
    }
    phase("done", "load");
    return module;
}

static int load_api(HMODULE module, radio_api *api, char *errors)
{
    /* Signatures match v2024.02.1 hackrf.h's explicit __cdecl convention. */
    api->init = (simple_fn)GetProcAddress(module, "hackrf_init");
    api->exit = (simple_fn)GetProcAddress(module, "hackrf_exit");
    api->open = (open_fn)GetProcAddress(module, "hackrf_open_by_serial");
    api->close = (close_fn)GetProcAddress(module, "hackrf_close");
    api->version = (version_fn)GetProcAddress(module, "hackrf_version_string_read");
    api->board_id = (board_id_fn)GetProcAddress(module, "hackrf_board_id_read");
    api->error_name = (error_name_fn)GetProcAddress(module, "hackrf_error_name");
    api->list = (list_fn)GetProcAddress(module, "hackrf_device_list");
    api->free_list = (free_list_fn)GetProcAddress(module, "hackrf_device_list_free");
    if (!api->init || !api->exit || !api->open || !api->close ||
        !api->version || !api->board_id || !api->error_name || !api->list || !api->free_list) {
        append_error(errors, "Required official HackRF export is missing");
        return 0;
    }
    return 1;
}

int wmain(int argc, wchar_t **argv)
{
    int idle = 0;
    int initialized = 0;
    int opened = 0;
    int argument;
    const wchar_t *dll_path = NULL;
    char serial[33] = {0};
    char errors[ERROR_CAPACITY] = {0};
    /* Official API adds a NUL after up to 255 returned firmware bytes. */
    char firmware[256] = {0};
    uint8_t board_id = 0;
    radio_api api = {0};
    hackrf_device *device = NULL;
    HMODULE module;

    if (argc == 2 && (wcscmp(argv[1], L"--help") == 0 ||
                      wcscmp(argv[1], L"-h") == 0)) {
        usage(stdout);
        return 0;
    }
    for (argument = 1; argument < argc; argument++) {
        if (wcscmp(argv[argument], L"--idle") == 0) {
            idle = 1;
        } else if (wcscmp(argv[argument], L"--dll") == 0 && argument + 1 < argc) {
            dll_path = argv[++argument];
        } else if (wcscmp(argv[argument], L"--serial") == 0 && argument + 1 < argc) {
            size_t digit;
            const wchar_t *value = argv[++argument];
            if (wcslen(value) != 32) {
                usage(stderr);
                return 2;
            }
            for (digit = 0; digit < 32; digit++) {
                if (value[digit] > 127 || !isxdigit((unsigned char)value[digit])) {
                    usage(stderr);
                    return 2;
                }
                serial[digit] = (char)tolower((unsigned char)value[digit]);
            }
        } else {
            usage(stderr);
            return 2;
        }
    }
    if (!dll_path || (idle && !serial[0])) {
        usage(stderr);
        return 2;
    }

    module = load_radio(dll_path, errors);
    if (module != NULL && load_api(module, &api, errors)) {
        phase("begin", "init");
        initialized = checked_call(&api, errors, "init", api.init());
        if (initialized && !serial[0]) {
            device_list *devices;
            phase("begin", "discovery");
            devices = api.list();
            if (!devices) {
                append_error(errors, "HackRF discovery failed");
            } else if (devices->devicecount != 1) {
                append_error(errors, "Found %d HackRF devices; automatic selection requires exactly one. Use --serial when multiple devices are connected.", devices->devicecount);
            } else if (!devices->serial_numbers || !devices->serial_numbers[0] ||
                       strlen(devices->serial_numbers[0]) != 32) {
                append_error(errors, "Connected HackRF did not provide a complete serial number");
            } else {
                size_t digit;
                for (digit = 0; digit < 32; digit++) {
                    unsigned char byte = (unsigned char)devices->serial_numbers[0][digit];
                    if (!isxdigit(byte)) {
                        append_error(errors, "Connected HackRF provided an invalid serial number");
                        break;
                    }
                    serial[digit] = (char)tolower(byte);
                }
                if (!errors[0])
                    phase("done", "discovery");
            }
            if (devices)
                api.free_list(devices);
        }
        if (initialized && !errors[0]) {
            phase("begin", "open");
            opened = checked_call(&api, errors, "open", api.open(serial, &device));
        }
        if (opened && !idle) {
            phase("begin", "board_id");
            if (checked_call(&api, errors, "board_id", api.board_id(device, &board_id)) &&
                board_id != BOARD_ID_HACKRF1_OG && board_id != BOARD_ID_HACKRF1_R9)
                append_error(errors, "This application requires HackRF One (board ID 2 or 4); device reports board ID %u", (unsigned int)board_id);
        }
        if (opened && !idle && !errors[0]) {
            phase("begin", "firmware");
            checked_call(&api, errors, "firmware", api.version(device, firmware, 255));
        }
        if (opened) {
            phase("begin", "close");
            checked_call(&api, errors, "close", api.close(device));
            device = NULL;
        }
        if (initialized) {
            phase("begin", "exit");
            checked_call(&api, errors, "exit", api.exit());
        }
    }
    if (module != NULL) {
        phase("begin", "unload");
        if (!FreeLibrary(module)) {
            append_error(errors, "DLL unload failed (Win32 %lu)", GetLastError());
        } else {
            phase("done", "unload");
        }
    }

    if (errors[0]) {
        fputs("{\"error\":", stdout);
        json_string(errors);
        fputs("}\n", stdout);
        fflush(stdout);
        return 1;
    }
    if (idle) {
        fputs("{\"idle\":true,\"serial\":", stdout);
        json_string(serial);
    } else {
        fputs("{\"name\":\"HackRF One\",\"serial\":", stdout);
        json_string(serial);
        fputs(",\"firmware\":", stdout);
        json_string(firmware);
    }
    fputs("}\n", stdout);
    fflush(stdout);
    return 0;
}
