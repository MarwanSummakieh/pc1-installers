/* Real modeless-dialog regression for 7-Zip's GetMessage/OnClose pattern.
 * https://github.com/ip7z/7zip/blob/main/C/Util/7zipInstall/7zipInstall.c
 * https://learn.microsoft.com/en-us/windows/win32/api/winuser/nf-winuser-getmessage
 * The parent proves a sent Close leaves a windowless live process, then wakes
 * only its own fixture with WM_NULL. Production action must queue Close and
 * obtain the child's actual exit code, never guess success from missing UI. */
#define wmain setup_bridge_main
#include "../os/files/usr/lib/marwanos/windows/setup_bridge.c"
#undef wmain

static HWND fixture_dialog;
static INT_PTR CALLBACK fixture_proc(HWND hwnd, UINT msg, WPARAM wp, LPARAM lp) {
    (void)lp;
    if (msg == WM_COMMAND && LOWORD(wp) == IDOK) {
        DestroyWindow(hwnd);
        fixture_dialog = NULL;
        return TRUE;
    }
    /* Deliberately no WM_DESTROY/PostQuitMessage, matching the installer. */
    return FALSE;
}

static int child(void) {
    struct { DLGTEMPLATE dialog; WORD menu, cls, title; } layout = {0};
    layout.dialog.style = WS_POPUP | WS_VISIBLE | WS_CAPTION | DS_MODALFRAME;
    layout.dialog.cx = 200; layout.dialog.cy = 90;
    fixture_dialog = CreateDialogIndirectParamW(GetModuleHandleW(NULL),
        &layout.dialog, NULL, fixture_proc, 0);
    if (!fixture_dialog) return 61;
    SetWindowTextW(fixture_dialog, L"PC1 modeless exit fixture");
    SetWindowPos(fixture_dialog, NULL, -10000, -10000, 320, 150, SWP_NOZORDER);
    HWND close = CreateWindowW(L"Button", L"Close", WS_CHILD | WS_VISIBLE | BS_PUSHBUTTON,
        100, 50, 80, 25, fixture_dialog, (HMENU)(uintptr_t)IDOK, NULL, NULL);
    if (!close) return 62;
    MSG msg;
    BOOL result;
    while ((result = GetMessageW(&msg, NULL, 0, 0)) != 0) {
        if (result == -1) return 63;
        if (!fixture_dialog) return 0;
        if (!IsDialogMessageW(fixture_dialog, &msg)) {
            TranslateMessage(&msg);
            DispatchMessageW(&msg);
        }
        if (!fixture_dialog) return 0;
    }
    return 64;
}

static int launch_child(PROCESS_INFORMATION *process) {
    wchar_t executable[MAX_PATH], command[MAX_PATH + 4];
    GetModuleFileNameW(NULL, executable, MAX_PATH);
    _snwprintf(command, MAX_PATH + 4, L"\"%ls\"", executable);
    STARTUPINFOW startup = {.cb = sizeof(startup)};
    if (!CreateProcessW(NULL, command, NULL, NULL, FALSE, 0, NULL, NULL, &startup, process)) return 1;
    pid_count = 1; pids[0] = process->dwProcessId;
    for (int n = 0; n < 100; n++) {
        enumerate();
        if (page && count) { Sleep(300); return 0; }
        Sleep(50);
    }
    return 1;
}

static void write_action(const wchar_t *path, uint32_t revision, uint64_t id) {
    FILE *file = _wfopen(path, L"wb");
    if (!file) exit(65);
    uint32_t verb = 1, length = 0;
    fwrite(&revision, 4, 1, file); fwrite(&id, 8, 1, file);
    fwrite(&verb, 4, 1, file); fwrite(&length, 4, 1, file);
    fclose(file);
}

static int verify(int queued, const wchar_t *action_path) {
    PROCESS_INFORMATION process = {0};
    int failed = launch_child(&process);
    if (!failed) {
        enumerate();
        Control *button = NULL;
        for (int i = 0; i < count; i++)
            if (!wcscmp(controls[i].text, L"Close")) button = &controls[i];
        if (!button) failed = 1;
        else if (queued) {
            uint64_t id = (uint64_t)(uintptr_t)button->hwnd;
            write_action(action_path, fingerprint ^ 1, id);
            action(action_path);
            failed |= !*action_error || !IsWindow(button->hwnd);
            write_action(action_path, fingerprint, id);
            action(action_path);
            failed |= *action_error || WaitForSingleObject(process.hProcess, 5000) != WAIT_OBJECT_0;
        } else {
            message(button->hwnd, BM_CLICK, 0, 0);
            failed |= IsWindow(button->hwnd);
            failed |= WaitForSingleObject(process.hProcess, 600) != WAIT_TIMEOUT;
            DWORD code = 0;
            failed |= !GetExitCodeProcess(process.hProcess, &code) || code != STILL_ACTIVE;
            /* This is fixture cleanup, not a setup-success fallback. */
            failed |= !PostThreadMessageW(process.dwThreadId, WM_NULL, 0, 0);
            failed |= WaitForSingleObject(process.hProcess, 5000) != WAIT_OBJECT_0;
        }
        DWORD code = STILL_ACTIVE;
        failed |= !GetExitCodeProcess(process.hProcess, &code) || code != 0;
    }
    if (process.hProcess) {
        if (WaitForSingleObject(process.hProcess, 0) != WAIT_OBJECT_0) TerminateProcess(process.hProcess, 66);
        CloseHandle(process.hProcess); CloseHandle(process.hThread);
    }
    printf("%s: %s Close, actual child exit zero\n", failed ? "FAIL" : "PASS", queued ? "production queued" : "sent Close blocks until fixture wake");
    return failed;
}

int wmain(int argc, wchar_t **argv) {
    if (argc == 1) return child();
    if (argc != 3 || wcscmp(argv[1], L"--check")) return 64;
    CoInitialize(NULL);
    int failed = verify(0, argv[2]) + verify(1, argv[2]);
    return failed ? 1 : 0;
}
