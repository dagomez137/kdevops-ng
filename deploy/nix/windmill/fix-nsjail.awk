BEGIN { inserted_store = 0 }

# Add the /nix/store bind mount once, just before the first mount block, so the
# nix-store-linked interpreters are visible inside the nsjail sandbox.
/^mount[ \t]*\{/ && !inserted_store {
    print "mount {"
    print "    src: \"/nix/store\""
    print "    dst: \"/nix/store\""
    print "    is_bind: true"
    print "}"
    print ""
    inserted_store = 1
}

# Buffer each mount block; on close, make the host system dirs non-mandatory
# (/bin, /lib, /usr do not all exist on NixOS, so a mandatory bind would fail).
/^mount[ \t]*\{/ { inblock = 1; n = 0; dst = ""; isbind = 0; hasmand = 0 }
inblock {
    buf[n++] = $0
    if ($0 ~ /dst:/) dst = $0
    if ($0 ~ /is_bind:[ \t]*true/) isbind = 1
    if ($0 ~ /mandatory:/) hasmand = 1
    if ($0 ~ /^\}/) {
        needmand = (isbind && !hasmand && dst ~ /"(\/bin|\/lib|\/usr)"/)
        for (i = 0; i < n - 1; i++) print buf[i]
        if (needmand) print "    mandatory: false"
        print buf[n - 1]
        inblock = 0
    }
    next
}
{ print }
