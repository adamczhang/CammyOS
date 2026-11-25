#!/bin/sh
# REVIEW ARTIFACT, shipped 0644. Run only after Adam approves this exact change.
# Changes ONE internal file. Does not start/stop an app, reboot, or touch TF/config.
# Usage after approval: /bin/sh transaction.review.sh install|restore PRIVATE_KIT_DIR
set -eu
PATH=/bin:/sbin:/usr/bin:/usr/sbin
export PATH
TARGET=/etc/init.d/S01app
APP=/usr/bin/sdvcam
PROCFS=/proc
EXPECT_UID=0
EXPECT_GID=0
STOCK_MD5=daeb505e811834088f64cb07696c5cbe
STOCK_SIZE=4246
NEXT_MD5=502b58d52476d863aa58058c7606b70b
NEXT_SIZE=40784
APP_MD5=8689ce53c4674563a7a6935f97c0d727
APP_SIZE=11335176

fail() { echo "bootstrap transaction refused: $*" >&2; exit 1; }
matches() {
    [ -f "$1" ] && [ ! -L "$1" ] || return 1
    size=$(wc -c <"$1") || return 1
    [ "$size" -eq "$3" ] || return 1
    digest=$(md5sum "$1" | cut -c1-32) || return 1
    [ "$digest" = "$2" ]
}
private_dir() {
    [ -d "$1" ] && [ ! -L "$1" ] || return 1
    ls -ldn "$1" | awk -v u="$EXPECT_UID" -v g="$EXPECT_GID" \
        '$1 == "drwx------" && $3 == u && $4 == g {ok=1} END {exit !ok}'
}
owned_target() {
    [ -d "${TARGET%/*}" ] && [ ! -L "${TARGET%/*}" ] || return 1
    ls -ldn "${TARGET%/*}" | awk -v u="$EXPECT_UID" -v g="$EXPECT_GID" \
        '$1 == "drwxr-xr-x" && $3 == u && $4 == g {ok=1} END {exit !ok}' || return 1
    owned_file "$TARGET"
}
owned_file() {
    [ -f "$1" ] && [ ! -L "$1" ] || return 1
    ls -ldn "$1" | awk -v u="$EXPECT_UID" -v g="$EXPECT_GID" \
        '$1 == "-rwxr-xr-x" && $3 == u && $4 == g {ok=1} END {exit !ok}'
}

[ "$#" -eq 2 ] || fail 'expected install|restore and private kit directory'
action=$1
kit=$2
case "$kit" in /*) ;; *) fail 'kit path must be absolute' ;; esac
awk -v u="$EXPECT_UID" -v g="$EXPECT_GID" '
    /^Uid:/ {uid=($3 == u)} /^Gid:/ {gid=($3 == g)}
    END {exit !(uid && gid)}' "$PROCFS/self/status" || fail 'root identity required'
private_dir "$kit" || fail 'kit must be private, owned directory'
owned_target || fail 'target must be owned 0755 regular file in real directory'
matches "$APP" "$APP_MD5" "$APP_SIZE" || fail 'stock app identity mismatch'
matches "$kit/S01app.stock" "$STOCK_MD5" "$STOCK_SIZE" || fail 'rollback copy mismatch'
matches "$kit/S01app.proposal" "$NEXT_MD5" "$NEXT_SIZE" || fail 'proposal mismatch'
case "$action" in
    install)
        src=$kit/S01app.proposal
        before_md5=$STOCK_MD5; before_size=$STOCK_SIZE
        after_md5=$NEXT_MD5; after_size=$NEXT_SIZE
        ;;
    restore)
        src=$kit/S01app.stock
        before_md5=$NEXT_MD5; before_size=$NEXT_SIZE
        after_md5=$STOCK_MD5; after_size=$STOCK_SIZE
        ;;
    *) fail 'unknown action' ;;
esac
matches "$TARGET" "$before_md5" "$before_size" || fail 'current target identity mismatch'
/bin/sh -n "$src" || fail 'source syntax rejected'

# A private lock directory serializes transactions; a crash leaves a blocking lock.
# Temp and destination are on the same filesystem for rename. Never reuse a stale lock.
lock=$TARGET.cammy-transaction
tmp=$lock/next
umask 077
mkdir "$lock" || fail 'transaction directory exists; inspect before retry'
cleanup() { rm -f "$tmp"; rm -r "$lock"; }
trap cleanup EXIT
trap 'exit 1' HUP INT TERM
(set -C; cat "$src" >"$tmp") || fail 'exclusive temporary copy failed'
chmod 0755 "$tmp" || fail 'temporary mode failed'
# Root creates the file; verify actual ownership instead of requiring absent chown.
owned_file "$tmp" || fail 'temporary ownership/mode mismatch'
matches "$tmp" "$after_md5" "$after_size" || fail 'temporary identity mismatch'
/bin/sh -n "$tmp" || fail 'temporary syntax rejected'
sync || fail 'pre-rename sync failed'
matches "$TARGET" "$before_md5" "$before_size" || fail 'target changed during preparation'
mv -f "$tmp" "$TARGET" || fail 'same-filesystem rename failed'
sync || fail 'post-rename sync failed: do not reboot; inspect target'
matches "$TARGET" "$after_md5" "$after_size" || fail 'installed read-back mismatch'
owned_target || fail 'installed mode/ownership mismatch'
echo "bootstrap file $action verified; app unchanged; no reboot or app launch performed"
# sync/read-back do not prove JFFS2 power-loss durability or recovery without USB.
