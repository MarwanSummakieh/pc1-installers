/* Exercises directory defaults and recognition of the native speaker control. */
#define wmain setup_bridge_main
#include "../os/files/usr/lib/marwanos/windows/setup_bridge.c"
#undef wmain

static int check_default(const wchar_t *initial, const wchar_t *expected) {
    HWND edit = CreateWindowW(L"Edit", initial, 0, 0, 0, 200, 30, NULL, NULL, NULL, NULL);
    if (!edit) return 1;
    Control c = {0};
    c.hwnd = edit;
    wcscpy(c.cls, L"TNewEdit");
    c.text = text_of(edit);
    destination_count = 0;
    default_directory(&c);
    free(c.text);
    c.text = text_of(edit);
    int failed = !c.destination || wcscmp(c.text, expected);
    free(c.text);
    /* Re-enumeration must not overwrite a controller-entered destination. */
    SetWindowTextW(edit, L"C:\\Custom\\Chosen folder");
    c.text = text_of(edit);
    default_directory(&c);
    failed |= c.text == NULL;
    free(c.text);
    c.text = text_of(edit);
    failed |= wcscmp(c.text, L"C:\\Custom\\Chosen folder") != 0;
    free(c.text);
    DestroyWindow(edit);
    return failed;
}

int wmain(void) {
    wcscpy(default_destination, L"C:\\Games");
    int failures = 0;
    wchar_t writable[2] = {0};
    if (GetEnvironmentVariableW(L"MARWANOS_SETUP_TEST_WRITABLE", writable, 2) == 1 && writable[0] == L'1') {
        wchar_t temporary[MAX_PATH];
        if (!GetTempFileNameW(default_destination, L"pc1", 0, temporary)) failures++;
        else {
            HANDLE file = CreateFileW(temporary, GENERIC_WRITE, 0, NULL, OPEN_EXISTING, FILE_ATTRIBUTE_NORMAL, NULL);
            DWORD written = 0;
            if (file == INVALID_HANDLE_VALUE || !WriteFile(file, "writable", 8, &written, NULL) || written != 8) failures++;
            if (file != INVALID_HANDLE_VALUE) CloseHandle(file);
            if (!DeleteFileW(temporary)) failures++;
        }
    }
    failures += check_default(L"D:\\Games\\Fixture Game", L"C:\\Games\\Fixture Game");
    failures += check_default(L"C:\\Program Files\\Game Name", L"C:\\Games\\Game Name");
    failures += check_default(L"D:\\Games", L"C:\\Games");
    failures += check_default(L"C:\\Program Files (x86)", L"C:\\Games");
    wchar_t command[512];
    failures += !installer_command(command, 512, L"Z:\\Game Setup\\setup.exe", 1);
    failures += wcscmp(command, L"\"Z:\\Game Setup\\setup.exe\" /DIR=\"C:\\Games\"") != 0;
    failures += !installer_command(command, 512, L"Z:\\Other Setup\\setup.exe", 0);
    failures += wcscmp(command, L"\"Z:\\Other Setup\\setup.exe\"") != 0;
    failures += !installer_command(command, 512, L"Z:\\Setup\\setup.msi", 1);
    failures += wcscmp(command, L"msiexec.exe /i \"Z:\\Setup\\setup.msi\"") != 0;
    failures += installer_command(command, 8, L"Z:\\Setup\\setup.exe", 1);
    failures += installer_command(command, 512, L"Z:\\Bad\"Setup.exe", 1);
    Control other = {0};
    wcscpy(other.cls, L"TNewEdit");
    other.text = L"Start Menu Folder";
    failures += directory_field(&other);
    wcscpy(other.cls, L"TEdit");
    other.text = L"D:\\Games\\TEKKEN 8";
    fitgirl_source = 1;
    failures += !directory_field(&other);
    fitgirl_source = 0;
    failures += directory_field(&other);
    page = CreateWindowW(L"Static", L"Fixture setup", 0, 100, 100, 503, 385, NULL, NULL, NULL, NULL);
    Control speaker = {0};
    wcscpy(speaker.cls, L"Button");
    speaker.kind = "button";
    speaker.text = L"";
    speaker.rect = (RECT){123, 442, 159, 478};
    fitgirl_source = 1;
    failures += !music_button(&speaker);
    fitgirl_source = 0;
    failures += music_button(&speaker);
    fitgirl_source = 1;
    speaker.text = L"Cancel";
    failures += music_button(&speaker);
    speaker.text = L"";
    speaker.rect.left = 400;
    speaker.rect.right = 436;
    failures += music_button(&speaker);
    failures += !fitgirl_name(L"Z:\\Downloads\\TEKKEN 8 [FITGIRL Repack]\\setup.exe");
    failures += fitgirl_name(L"Z:\\Downloads\\Other installer\\setup.exe");
    DestroyWindow(page);
    wcscpy(other.cls, L"Edit");
    other.text = L"C:\\Some other field";
    failures += directory_field(&other);
    printf("Setup destination and music checks: %d failure(s)\n", failures);
    return failures ? 1 : 0;
}
