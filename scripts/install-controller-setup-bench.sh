#!/usr/bin/env bash
# Activate a verified controller-setup export on the existing development bench.
set -euo pipefail
test "$(id -u)" = 0
test -f /var/marwanos/bench-fixes-20261005/router.py
stage=/var/tmp/pc1-guided
target=/var/marwanos/controller-setup-20261005
unit=/etc/systemd/system/marwanos-bench-fixes.service
backup="$unit.before-controller-setup-20261005"
test -f "$unit"
test ! -e "$backup"
test ! -e "$target"
for file in manager.py setup-bridge.exe marwanos-shell; do
    test -s "$stage/$file"
done
old_pid=$(pgrep -u player -f '^/usr/lib/marwanos/shell/marwanos-shell$')
test "$(printf '%s\n' "$old_pid" | wc -l)" = 1
install -d -m 0755 "$target/windows"
install -m 0755 "$stage/manager.py" "$target/windows/manager.py"
install -m 0644 "$stage/setup-bridge.exe" "$target/windows/setup-bridge.exe"
install -m 0644 /usr/lib/marwanos/windows/recipes.json "$target/windows/recipes.json"
install -m 0755 "$stage/marwanos-shell" "$target/marwanos-shell"
chcon --reference=/var/marwanos/bench-fixes-20261005/marwanos-shell "$target/marwanos-shell"
python3 -m py_compile "$target/windows/manager.py"
runuser -u player -- env MARWANOS_MOWSER_ROOT=/usr/lib/marwanos/mowser \
    LD_LIBRARY_PATH=/usr/lib/marwanos/mowser XDG_RUNTIME_DIR=/run/user/1000 \
    "$target/marwanos-shell" --headless --audio-driver Dummy --quit-after 4 \
    > "$stage/export-preflight.log" 2>&1
! grep -qE 'SCRIPT ERROR|Parse Error|Failed to load script|Failed loading resource' "$stage/export-preflight.log"
grep -q 'home rail ready' "$stage/export-preflight.log"
cp -a "$unit" "$backup"
cat > "$stage/bench-unit.new" <<'EOF'
[Unit]
Description=PC1 bench controller fixes and controller Windows setup
After=local-fs.target
Before=greetd.service marwanos-windows.service
ConditionPathExists=/var/marwanos/controller-setup-20261005/windows/setup-bridge.exe

[Service]
Type=oneshot
RemainAfterExit=yes
ExecStart=/usr/bin/mount --bind /var/marwanos/bench-fixes-20261005/router.py /usr/lib/marwanos/controller/router.py
ExecStart=/usr/bin/mount --bind /var/marwanos/controller-setup-20261005/windows /usr/lib/marwanos/windows
ExecStart=/usr/bin/mount --bind /var/marwanos/controller-setup-20261005/marwanos-shell /usr/lib/marwanos/shell/marwanos-shell
ExecStop=/usr/bin/umount -l /usr/lib/marwanos/shell/marwanos-shell
ExecStop=/usr/bin/umount /usr/lib/marwanos/windows
ExecStop=/usr/bin/umount /usr/lib/marwanos/controller/router.py

[Install]
WantedBy=multi-user.target
EOF
systemctl stop marwanos-bench-fixes.service
install -m 0644 "$stage/bench-unit.new" "$unit"
systemctl daemon-reload
if ! systemctl start marwanos-bench-fixes.service; then
    systemctl stop marwanos-bench-fixes.service || true
    cp -a "$backup" "$unit"
    systemctl daemon-reload
    systemctl start marwanos-bench-fixes.service
    exit 1
fi
kill -TERM "$old_pid"
for attempt in $(seq 1 30); do
    new_pid=$(pgrep -u player -f '^/usr/lib/marwanos/shell/marwanos-shell$' || true)
    if test -n "$new_pid" && test "$new_pid" != "$old_pid"; then
        printf 'Controller setup activated; shell PID %s.\n' "$new_pid"
        exit 0
    fi
    sleep 0.5
done
echo 'The interface is mounted, but the shell supervisor did not restart it.' >&2
exit 1
