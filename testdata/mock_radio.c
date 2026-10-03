/* Offline native-control fixture. No USB, libusb, RF, or hardware functions. */
#include <stdint.h>
#include <stdlib.h>
#include <string.h>

#define EXPORT __declspec(dllexport)
typedef struct {
    char **serial_numbers;
    int *usb_board_ids;
    int *usb_device_index;
    int devicecount;
    void **usb_devices;
    int usb_devicecount;
} device_list;

static char valid_serial[] = "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa";
static char *serials[2] = { valid_serial, valid_serial };
static device_list devices;

EXPORT int __cdecl hackrf_init(void) { return 0; }
EXPORT int __cdecl hackrf_exit(void) { return 0; }
EXPORT device_list *__cdecl hackrf_device_list(void) {
    char *count = getenv("ANOMALY_TEST_DEVICE_COUNT");
    char *serial = getenv("ANOMALY_TEST_SERIAL");
    devices.devicecount = count ? atoi(count) : 1;
    serials[0] = serial ? serial : valid_serial;
    devices.serial_numbers = serials;
    return &devices;
}
EXPORT void __cdecl hackrf_device_list_free(device_list *list) { (void)list; }
EXPORT int __cdecl hackrf_open_by_serial(const char *serial, void **device) {
    (void)serial;
    *device = (void *)(uintptr_t)1;
    return 0;
}
EXPORT int __cdecl hackrf_close(void *device) { (void)device; return 0; }
EXPORT int __cdecl hackrf_board_id_read(void *device, uint8_t *id) {
    char *board = getenv("ANOMALY_TEST_BOARD_ID");
    (void)device;
    *id = (uint8_t)(board ? atoi(board) : 2);
    return 0;
}
EXPORT int __cdecl hackrf_version_string_read(void *device, char *output, uint8_t length) {
    (void)device;
    if (length < 14) return -2;
    strcpy_s(output, (size_t)length, "offline-fixture");
    return 0;
}
EXPORT const char *__cdecl hackrf_error_name(int code) { (void)code; return "mock error"; }
