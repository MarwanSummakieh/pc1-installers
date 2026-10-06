/* PC1 setup adapter. Built for Windows, run beside setup inside its Wine prefix.
 * No installation options are guessed: only live, visible controls are exposed.
 * A page fingerprint and a freshly enumerated allowlist reject stale actions. */
#ifndef UNICODE
#define UNICODE
#endif
#ifndef _UNICODE
#define _UNICODE
#endif
#include <windows.h>
#include <commctrl.h>
#define COBJMACROS
#include <oleacc.h>
#include <tlhelp32.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <wchar.h>

#define MAX_CONTROLS 512
#define MAX_TEXT 262144
typedef struct {
    HWND hwnd;
    wchar_t cls[128];
    wchar_t *text;
    RECT rect;
    const char *kind;
    int enabled, checked, pos, range, destination, music;
    int child, selected, option_count;
    wchar_t **options;
    IAccessible *accessible;
} Control;
static Control controls[MAX_CONTROLS];
static int count;
static HWND page;
static DWORD pids[1024];
static int pid_count;
static wchar_t title[1024];
static const char *action_error = "";
static uint32_t fingerprint;
static wchar_t default_destination[32768], host_directory[32768];
static HWND destination_fields[MAX_CONTROLS];
static int destination_count;
static int fitgirl_source;

static int fitgirl_name(const wchar_t *name) {
    for (const wchar_t *p = name; *p; p++)
        if (!_wcsnicmp(p, L"fitgirl", 7)) return 1;
    return 0;
}

static int directory_field(Control *c) {
    /* Inno's destination field is an absolute path, unlike its Start Menu edit.
     * This FitGirl build uses TEdit for the same destination control. */
    return (!_wcsicmp(c->cls, L"TNewEdit") || (fitgirl_source && !_wcsicmp(c->cls, L"TEdit"))) && wcslen(c->text) > 3
        && c->text[1] == L':' && (c->text[2] == L'\\' || c->text[2] == L'/');
}
static void default_directory(Control *c) {
    c->destination = directory_field(c);
    if (!c->destination || !*default_destination) return;
    for (int i = 0; i < destination_count; i++)
        if (destination_fields[i] == c->hwnd) return;
    if (destination_count >= MAX_CONTROLS) return;
    destination_fields[destination_count++] = c->hwnd;
    wchar_t path[32768];
    const wchar_t *leaf = wcsrchr(c->text, L'\\');
    leaf = leaf ? leaf + 1 : L"";
    /* Retain the repack's own game folder name, never its drive default. */
    if (!*leaf || !_wcsicmp(leaf, L"Games") || !_wcsicmp(leaf, L"Program Files")
            || !_wcsicmp(leaf, L"Program Files (x86)")) leaf = L"";
    swprintf(path, 32768, L"%ls%ls%ls", default_destination, *leaf ? L"\\" : L"", leaf);
    DWORD_PTR answer;
    SendMessageTimeoutW(c->hwnd, WM_SETTEXT, 0, (LPARAM)path,
        SMTO_ABORTIFHUNG | SMTO_BLOCK, 200, &answer);
    free(c->text); c->text = NULL;
}

static int installer_command(wchar_t *command, size_t capacity, const wchar_t *source, int inno) {
    /* Initialize Inno's destination before it validates a drive or shows a page.
     * The manager verifies the loader signature and prepares writable C:\Games. */
    if (wcschr(source, L'"') || wcschr(default_destination, L'"')) return 0;
    size_t length = wcslen(source);
    int result;
    if (length >= 4 && !_wcsicmp(source + length - 4, L".msi"))
        result = _snwprintf(command, capacity, L"msiexec.exe /i \"%ls\"", source);
    else if (inno && *default_destination)
        result = _snwprintf(command, capacity, L"\"%ls\" /DIR=\"%ls\"", source, default_destination);
    else result = _snwprintf(command, capacity, L"\"%ls\"", source);
    return result >= 0 && (size_t)result < capacity;
}

static LRESULT message(HWND hwnd, UINT msg, WPARAM wp, LPARAM lp) {
    DWORD_PTR answer = 0;
    SendMessageTimeoutW(hwnd, msg, wp, lp, SMTO_ABORTIFHUNG | SMTO_BLOCK, 200, &answer);
    return (LRESULT)answer;
}
static wchar_t *text_of(HWND hwnd) {
    int length = (int)message(hwnd, WM_GETTEXTLENGTH, 0, 0);
    if (length < 0) length = 0;
    if (length > MAX_TEXT) length = MAX_TEXT;
    wchar_t *text = calloc((size_t)length + 2, sizeof(wchar_t));
    if (text) message(hwnd, WM_GETTEXT, length + 1, (LPARAM)text);
    return text;
}
static int known_pid(DWORD pid) {
    for (int i = 0; i < pid_count; i++) if (pids[i] == pid) return 1;
    return 0;
}
static void discover_children(void) {
    HANDLE snapshot = CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0);
    PROCESSENTRY32W entry = {.dwSize = sizeof(entry)};
    /* Multiple passes also cover a bootstrapper and its grandchild. */
    for (int pass = 0; pass < 4; pass++) {
        if (Process32FirstW(snapshot, &entry)) do {
            if (!known_pid(entry.th32ProcessID) && known_pid(entry.th32ParentProcessID)
                    && pid_count < 1024) pids[pid_count++] = entry.th32ProcessID;
        } while (Process32NextW(snapshot, &entry));
    }
    CloseHandle(snapshot);
}
static BOOL CALLBACK find_page(HWND hwnd, LPARAM unused) {
    (void)unused;
    DWORD pid;
    GetWindowThreadProcessId(hwnd, &pid);
    if (!known_pid(pid) || !IsWindowVisible(hwnd)) return TRUE;
    /* An enabled modal dialog wins over its disabled parent. */
    if (!page || (IsWindowEnabled(hwnd) && !IsWindowEnabled(page))) page = hwnd;
    HWND popup = GetLastActivePopup(hwnd);
    if (popup != hwnd && IsWindowVisible(popup) && IsWindowEnabled(popup)) page = popup;
    return TRUE;
}
static int contains(const wchar_t *text, const wchar_t *part) {
    return wcsstr(text, part) != NULL;
}
static int music_button(Control *c) {
    /* The verified FitGirl speaker is an unnamed Win32 Button at the left of
     * the footer. Do not relabel arbitrary unnamed controls in other setups. */
    RECT parent;
    if (!fitgirl_source || _wcsicmp(c->cls, L"Button") || *c->text
            || strcmp(c->kind, "button") || !GetWindowRect(page, &parent)) return 0;
    int width = c->rect.right - c->rect.left, height = c->rect.bottom - c->rect.top;
    return width >= 20 && width <= 64 && height >= 20 && height <= 64
        && abs(width - height) <= 4 && c->rect.left - parent.left < 80
        && c->rect.top >= parent.bottom - 80;
}
static BOOL CALLBACK read_control(HWND hwnd, LPARAM unused) {
    (void)unused;
    if (count >= MAX_CONTROLS || !IsWindowVisible(hwnd)) return TRUE;
    Control c = {0};
    c.hwnd = hwnd;
    GetClassNameW(hwnd, c.cls, 128);
    c.text = text_of(hwnd);
    if (!c.text) return TRUE;
    GetWindowRect(hwnd, &c.rect);
    c.enabled = IsWindowEnabled(hwnd);
    LONG_PTR style = GetWindowLongPtrW(hwnd, GWL_STYLE);
    c.kind = "text";
    if (!_wcsicmp(c.cls, L"Button") || contains(c.cls, L"Button")
            || contains(c.cls, L"CheckBox") || contains(c.cls, L"RadioButton")) {
        int type = style & BS_TYPEMASK;
        if (type == BS_GROUPBOX) c.kind = "text";
        else if (type == BS_CHECKBOX || type == BS_AUTOCHECKBOX || type == BS_3STATE
                || type == BS_AUTO3STATE || contains(c.cls, L"CheckBox")) c.kind = "check";
        else if (type == BS_RADIOBUTTON || type == BS_AUTORADIOBUTTON
                || contains(c.cls, L"RadioButton")) c.kind = "radio";
        else c.kind = "button";
        c.checked = (int)message(hwnd, BM_GETCHECK, 0, 0);
    } else if (!_wcsicmp(c.cls, L"Edit") || contains(c.cls, L"Edit")
            || contains(c.cls, L"Memo") || contains(c.cls, L"RichEdit")) {
        c.kind = (style & ES_READONLY) ? "text" : "edit";
        /* Never copy password fields into a snapshot. */
        if (style & ES_PASSWORD) { free(c.text); c.text = _wcsdup(L""); c.kind = "unsupported"; }
        else if (!strcmp(c.kind, "edit")) {
            default_directory(&c);
            if (!c.text) c.text = text_of(hwnd);
        }
    } else if (contains(c.cls, L"Progress") || contains(c.cls, L"progress")) {
        c.kind = "progress";
        c.pos = (int)message(hwnd, PBM_GETPOS, 0, 0);
        c.range = (int)message(hwnd, PBM_GETRANGE, FALSE, 0);
    } else if (contains(c.cls, L"Combo")) {
        c.kind = "combo";
        c.selected = (int)message(hwnd, CB_GETCURSEL, 0, 0);
        int n = (int)message(hwnd, CB_GETCOUNT, 0, 0);
        if (n < 0 || n > 256) c.kind = "unsupported";
        else {
            c.option_count = n;
            c.options = calloc(n, sizeof(wchar_t *));
            for (int i = 0; i < n; i++) {
                int length = (int)message(hwnd, CB_GETLBTEXTLEN, i, 0);
                if (length < 0 || length > 4096) { c.kind = "unsupported"; c.options[i] = _wcsdup(L""); }
                else {
                    c.options[i] = calloc(length + 2, sizeof(wchar_t));
                    message(hwnd, CB_GETLBTEXT, i, (LPARAM)c.options[i]);
                }
            }
        }
    } else if (contains(c.cls, L"List") || contains(c.cls, L"Tree")) {
        IAccessible *accessible = NULL;
        LONG children = 0;
        if (SUCCEEDED(AccessibleObjectFromWindow(hwnd, OBJID_CLIENT, &IID_IAccessible, (void **)&accessible))
                && SUCCEEDED(IAccessible_get_accChildCount(accessible, &children)) && children > 0 && children < 256) {
            int added = 0;
            for (LONG i = 1; i <= children && count < MAX_CONTROLS; i++) {
                VARIANT child = {.vt = VT_I4, .lVal = i}, role, state;
                VariantInit(&role); VariantInit(&state);
                BSTR name = NULL;
                IAccessible_get_accRole(accessible, child, &role);
                IAccessible_get_accState(accessible, child, &state);
                IAccessible_get_accName(accessible, child, &name);
                if (role.vt == VT_I4 && state.vt == VT_I4 && name
                        && (role.lVal == ROLE_SYSTEM_CHECKBUTTON || role.lVal == ROLE_SYSTEM_RADIOBUTTON)) {
                    Control item = c;
                    item.text = _wcsdup(name);
                    item.kind = role.lVal == ROLE_SYSTEM_CHECKBUTTON ? "check" : "radio";
                    item.child = i;
                    item.checked = !!(state.lVal & STATE_SYSTEM_CHECKED);
                    item.enabled = c.enabled && !(state.lVal & STATE_SYSTEM_UNAVAILABLE);
                    item.rect.top += i;
                    item.accessible = accessible;
                    IAccessible_AddRef(accessible);
                    controls[count++] = item;
                    added++;
                }
                SysFreeString(name); VariantClear(&role); VariantClear(&state);
            }
            IAccessible_Release(accessible);
            if (added == children) { free(c.text); return TRUE; }
        } else if (accessible) IAccessible_Release(accessible);
        c.kind = !_wcsicmp(c.cls, L"TNewCheckListBox") ? "list" : "unsupported";
        if (!strcmp(c.kind, "list")) c.selected = (int)message(hwnd, LB_GETCURSEL, 0, 0);
    }
    /* Panels and graphics are layout, not duplicate page copy. */
    else if (contains(c.cls, L"Panel") || contains(c.cls, L"Form")
            || contains(c.cls, L"Bitmap") || contains(c.cls, L"Bevel") || contains(c.cls, L"Notebook")) {
        free(c.text); return TRUE;
    }
    /* WiX subclasses Static for its owner-drawn images and other read-only
     * decoration. It is not an unsupported interactive control. */
    else if (_wcsicmp(c.cls, L"Static") && _wcsicmp(c.cls, L"ThemeStaticOwnerDraw")
            && !contains(c.cls, L"StaticText") && !contains(c.cls, L"Label"))
        c.kind = "unsupported";
    c.music = music_button(&c);
    if (c.music) { free(c.text); c.text = _wcsdup(L"Installer music"); }
    if (!*c.text && !strcmp(c.kind, "text")) { free(c.text); return TRUE; }
    controls[count++] = c;
    return TRUE;
}
static int order(const void *left, const void *right) {
    const Control *a = left, *b = right;
    if (a->rect.top != b->rect.top) return a->rect.top < b->rect.top ? -1 : 1;
    return a->rect.left < b->rect.left ? -1 : a->rect.left > b->rect.left;
}
static void hash_bytes(const void *ptr, size_t size) {
    const unsigned char *bytes = ptr;
    while (size--) fingerprint = (fingerprint ^ *bytes++) * 16777619u;
}
static void enumerate(void) {
    for (int i = 0; i < count; i++) {
        free(controls[i].text);
        for (int j = 0; j < controls[i].option_count; j++) free(controls[i].options[j]);
        free(controls[i].options);
        if (controls[i].accessible) IAccessible_Release(controls[i].accessible);
    }
    count = 0; page = NULL; title[0] = 0;
    discover_children();
    EnumWindows(find_page, 0);
    if (page) {
        GetWindowTextW(page, title, 1024);
        EnumChildWindows(page, read_control, 0);
        qsort(controls, count, sizeof(Control), order);
    }
    fingerprint = 2166136261u;
    hash_bytes(&page, sizeof(page));
    hash_bytes(title, wcslen(title) * sizeof(wchar_t));
    for (int i = 0; i < count; i++) {
        Control *c = &controls[i];
        hash_bytes(&c->hwnd, sizeof(c->hwnd));
        hash_bytes(c->kind, strlen(c->kind));
        hash_bytes(&c->enabled, sizeof(c->enabled));
        hash_bytes(&c->checked, sizeof(c->checked));
        hash_bytes(&c->child, sizeof(c->child));
        hash_bytes(&c->selected, sizeof(c->selected));
        hash_bytes(c->text, wcslen(c->text) * sizeof(wchar_t));
        for (int j = 0; j < c->option_count; j++) hash_bytes(c->options[j], wcslen(c->options[j]) * sizeof(wchar_t));
    }
}
static void json_text(FILE *file, const wchar_t *wide) {
    int length = WideCharToMultiByte(CP_UTF8, 0, wide, -1, NULL, 0, NULL, NULL);
    char *text = calloc(length + 1, 1);
    WideCharToMultiByte(CP_UTF8, 0, wide, -1, text, length, NULL, NULL);
    fputc('"', file);
    for (unsigned char *p = (unsigned char *)text; *p; p++) {
        if (*p == '"' || *p == '\\') { fputc('\\', file); fputc(*p, file); }
        else if (*p < 32) fprintf(file, "\\u%04x", *p);
        else fputc(*p, file);
    }
    fputc('"', file); free(text);
}
static void capture_widget(Control *c, const wchar_t *snapshot) {
    if (c->music) RedrawWindow(c->hwnd, NULL, NULL, RDW_INVALIDATE | RDW_UPDATENOW);
    RECT rect;
    GetClientRect(c->hwnd, &rect);
    int width = rect.right, height = rect.bottom;
    int items = (int)message(c->hwnd, LB_GETCOUNT, 0, 0);
    RECT last;
    if (items > 0 && items < 256 && message(c->hwnd, LB_GETITEMRECT, items - 1, (LPARAM)&last) != LB_ERR)
        height = min(height, last.bottom + 8);
    if (width <= 0 || height <= 0 || width > 4096 || height > 4096) return;
    BITMAPINFO info = {0};
    info.bmiHeader.biSize = sizeof(BITMAPINFOHEADER);
    /* Godot's BMP loader needs the conventional bottom-up representation. */
    info.bmiHeader.biWidth = width; info.bmiHeader.biHeight = height;
    info.bmiHeader.biPlanes = 1; info.bmiHeader.biBitCount = 32;
    info.bmiHeader.biCompression = BI_RGB;
    HDC source = GetDC(c->hwnd), dc = CreateCompatibleDC(source);
    void *pixels;
    HBITMAP bitmap = CreateDIBSection(source, &info, DIB_RGB_COLORS, &pixels, NULL, 0);
    HGDIOBJ previous = SelectObject(dc, bitmap);
    BitBlt(dc, 0, 0, width, height, source, 0, 0, SRCCOPY);
    wchar_t path[32768], temporary[32768];
    wcscpy(path, snapshot);
    wchar_t *slash = wcsrchr(path, L'\\');
    if (slash) {
        swprintf(slash + 1, 32768 - (slash + 1 - path), L"widget-%llu.bmp", (unsigned long long)(uintptr_t)c->hwnd);
        swprintf(temporary, 32768, L"%ls.tmp", path);
        FILE *file = _wfopen(temporary, L"wb");
        if (file) {
            DWORD size = width * height * 4;
            BITMAPFILEHEADER header = {.bfType = 0x4d42, .bfSize = sizeof(header) + sizeof(BITMAPINFOHEADER) + size,
                                      .bfOffBits = sizeof(header) + sizeof(BITMAPINFOHEADER)};
            fwrite(&header, sizeof(header), 1, file);
            fwrite(&info.bmiHeader, sizeof(BITMAPINFOHEADER), 1, file);
            fwrite(pixels, 1, size, file); fclose(file);
            MoveFileExW(temporary, path, MOVEFILE_REPLACE_EXISTING);
        }
    }
    SelectObject(dc, previous); DeleteObject(bitmap); DeleteDC(dc); ReleaseDC(c->hwnd, source);
}
static void publish(const wchar_t *path, int finished, DWORD exit_code) {
    wchar_t temporary[32768];
    swprintf(temporary, 32768, L"%ls.tmp", path);
    FILE *file = _wfopen(temporary, L"wb");
    if (!file) return;
    fprintf(file, "{\"version\":1,\"page\":%u,\"finished\":%s,\"exit_code\":%lu,\"error\":\"%s\",\"title\":",
            fingerprint, finished ? "true" : "false", (unsigned long)exit_code, action_error);
    json_text(file, title);
    fprintf(file, ",\"host_directory\":"); json_text(file, host_directory);
    RECT page_rect = {0};
    if (page) GetWindowRect(page, &page_rect);
    fprintf(file, ",\"width\":%ld,\"height\":%ld,\"controls\":[",
        (long)(page_rect.right - page_rect.left), (long)(page_rect.bottom - page_rect.top));
    for (int i = 0; i < count; i++) {
        Control *c = &controls[i];
        if (!strcmp(c->kind, "list") || c->music) capture_widget(c, path);
        fprintf(file, "%s{\"id\":%llu,\"kind\":\"%s\",\"enabled\":%s,\"checked\":%d,\"position\":%d,\"range\":%d,\"class\":",
                i ? "," : "", (unsigned long long)((uint64_t)(uintptr_t)c->hwnd | ((uint64_t)c->child << 32)), c->kind,
                c->enabled ? "true" : "false", c->checked, c->pos, c->range);
        json_text(file, c->cls); fprintf(file, ",\"text\":"); json_text(file, c->text);
        fprintf(file, ",\"destination\":%s,\"music\":%s,\"rect\":[%ld,%ld,%ld,%ld],\"selected\":%d,\"options\":[",
            c->destination ? "true" : "false", c->music ? "true" : "false", (long)(c->rect.left - page_rect.left),
            (long)(c->rect.top - page_rect.top), (long)(c->rect.right - c->rect.left),
            (long)(c->rect.bottom - c->rect.top), c->selected);
        for (int j = 0; j < c->option_count; j++) { if (j) fputc(',', file); json_text(file, c->options[j]); }
        fprintf(file, "]}");
    }
    fprintf(file, "]}"); fclose(file);
    MoveFileExW(temporary, path, MOVEFILE_REPLACE_EXISTING | MOVEFILE_WRITE_THROUGH);
}
static void action(const wchar_t *path) {
    FILE *file = _wfopen(path, L"rb");
    if (!file) return;
    uint32_t revision, verb, length;
    uint64_t id;
    int valid = fread(&revision, 4, 1, file) == 1 && fread(&id, 8, 1, file) == 1
            && fread(&verb, 4, 1, file) == 1 && fread(&length, 4, 1, file) == 1;
    char *utf8 = NULL;
    if (valid && length <= 65536) {
        utf8 = calloc(length + 1, 1);
        valid = fread(utf8, 1, length, file) == length && fgetc(file) == EOF;
    } else valid = 0;
    fclose(file); DeleteFileW(path);
    action_error = "The setup page changed. Select the control again.";
    if (!valid || revision != fingerprint) { free(utf8); return; }
    for (int i = 0; i < count; i++) {
        Control *c = &controls[i];
        if (((uint64_t)(uintptr_t)c->hwnd | ((uint64_t)c->child << 32)) != id || !c->enabled) continue;
        if (verb == 1 && (!strcmp(c->kind, "button") || !strcmp(c->kind, "check") || !strcmp(c->kind, "radio"))) {
            /* BM_CLICK requires the containing dialog to be active. */
            SetForegroundWindow(page);
            if (c->music) {
                /* The music DLL handles mouse messages in its Button window
                 * procedure; BM_CLICK alone never reaches that handler. */
                DWORD thread = GetWindowThreadProcessId(c->hwnd, NULL);
                AttachThreadInput(GetCurrentThreadId(), thread, TRUE);
                SetActiveWindow(page); SetForegroundWindow(page); SetFocus(c->hwnd);
                AttachThreadInput(GetCurrentThreadId(), thread, FALSE);
                INPUT input[3] = {0};
                for (int n = 0; n < 3; n++) input[n].type = INPUT_MOUSE;
                input[0].mi.dx = (LONG)((int64_t)(c->rect.left + c->rect.right) / 2 * 65535
                    / max(1, GetSystemMetrics(SM_CXSCREEN) - 1));
                input[0].mi.dy = (LONG)((int64_t)(c->rect.top + c->rect.bottom) / 2 * 65535
                    / max(1, GetSystemMetrics(SM_CYSCREEN) - 1));
                input[0].mi.dwFlags = MOUSEEVENTF_MOVE | MOUSEEVENTF_ABSOLUTE;
                input[1].mi.dwFlags = MOUSEEVENTF_LEFTDOWN;
                input[2].mi.dwFlags = MOUSEEVENTF_LEFTUP;
                /* Wine's cursor warp crosses the X server before the DLL's
                 * hit test. Deliver the click after that move has settled. */
                if (SendInput(1, input, sizeof(INPUT)) != 1) {
                    action_error = "Could not toggle installer music. Try again.";
                    free(utf8); return;
                }
                Sleep(80);
                if (!IsWindowVisible(c->hwnd) || !IsWindowEnabled(c->hwnd)) {
                    free(utf8); return;
                }
                UINT down = SendInput(1, input + 1, sizeof(INPUT));
                Sleep(30);
                UINT up = SendInput(1, input + 2, sizeof(INPUT));
                if (down != 1 || up != 1) {
                    action_error = "Could not toggle installer music. Try again.";
                    free(utf8); return;
                }
                Sleep(80);
                RECT rect;
                GetClientRect(c->hwnd, &rect);
                /* This DLL refreshes its speaker graphic on mouse movement. */
                PostMessageW(c->hwnd, WM_MOUSEMOVE, 0, MAKELPARAM(rect.right / 2, rect.bottom / 2));
            } else if (c->accessible) {
                VARIANT child = {.vt = VT_I4, .lVal = c->child};
                IAccessible_accDoDefaultAction(c->accessible, child);
            } else message(c->hwnd, BM_CLICK, 0, 0);
            action_error = "";
        } else if (verb == 2 && !strcmp(c->kind, "edit")) {
            int size = MultiByteToWideChar(CP_UTF8, MB_ERR_INVALID_CHARS, utf8, -1, NULL, 0);
            if (size > 0) {
                wchar_t *wide = calloc(size, sizeof(wchar_t));
                MultiByteToWideChar(CP_UTF8, MB_ERR_INVALID_CHARS, utf8, -1, wide, size);
                message(c->hwnd, WM_SETTEXT, 0, (LPARAM)wide);
                free(wide); action_error = "";
            }
        } else if (verb == 3 && !strcmp(c->kind, "combo")) {
            char *end;
            long selection = strtol(utf8, &end, 10);
            if (length && !*end && selection >= 0 && selection < c->option_count) {
                message(c->hwnd, CB_SETCURSEL, selection, 0);
                message(GetParent(c->hwnd), WM_COMMAND, MAKEWPARAM(GetDlgCtrlID(c->hwnd), CBN_SELCHANGE), (LPARAM)c->hwnd);
                action_error = "";
            }
        } else if (verb == 4 && !strcmp(c->kind, "list")) {
            UINT key = !strcmp(utf8, "up") ? VK_UP : !strcmp(utf8, "down") ? VK_DOWN : !strcmp(utf8, "space") ? VK_SPACE : 0;
            if (key) {
                DWORD thread = GetWindowThreadProcessId(c->hwnd, NULL);
                AttachThreadInput(GetCurrentThreadId(), thread, TRUE);
                SetForegroundWindow(page); SetFocus(c->hwnd);
                AttachThreadInput(GetCurrentThreadId(), thread, FALSE);
                PostMessageW(c->hwnd, WM_KEYDOWN, key, 1 | (MapVirtualKeyW(key, MAPVK_VK_TO_VSC) << 16));
                PostMessageW(c->hwnd, WM_KEYUP, key, (LPARAM)0xc0000001 | (MapVirtualKeyW(key, MAPVK_VK_TO_VSC) << 16));
                action_error = "";
            }
        }
        break;
    }
    free(utf8);
}
int wmain(int argc, wchar_t **argv) {
    if (argc != 4) return 64;
    CoInitialize(NULL);
    fitgirl_source = fitgirl_name(argv[1]);
    GetEnvironmentVariableW(L"MARWANOS_SETUP_DESTINATION", default_destination, 32768);
    GetEnvironmentVariableW(L"MARWANOS_SETUP_HOST_DIRECTORY", host_directory, 32768);
    wchar_t command[32768];
    wchar_t inno[2] = {0};
    int identified_inno = GetEnvironmentVariableW(L"MARWANOS_SETUP_INNO", inno, 2) == 1 && inno[0] == L'1';
    if (!installer_command(command, 32768, argv[1], identified_inno)) return 64;
    STARTUPINFOW startup = {.cb = sizeof(startup)};
    PROCESS_INFORMATION process = {0};
    if (!CreateProcessW(NULL, command, NULL, NULL, FALSE, 0, NULL, NULL, &startup, &process)) return 65;
    pids[pid_count++] = process.dwProcessId;
    DWORD code = STILL_ACTIVE;
    int empty_ticks = 0;
    do {
        enumerate();
        action(argv[3]);
        GetExitCodeProcess(process.hProcess, &code);
        empty_ticks = (!page && code != STILL_ACTIVE) ? empty_ticks + 1 : 0;
        publish(argv[2], empty_ticks >= 10, code);
        Sleep(200);
    } while (empty_ticks < 10);
    CloseHandle(process.hProcess); CloseHandle(process.hThread);
    return (int)code;
}
