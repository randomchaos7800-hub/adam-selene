#!/bin/bash
# vault.sh — simple age-encrypted credential store
# Usage:
#   vault.sh get <key>         — retrieve a value (prints to stdout)
#   vault.sh set <key>         — store a value (reads from stdin)
#   vault.sh list              — list all keys
#   vault.sh edit              — open decrypted vault in editor
#
# Setup:
#   mkdir -p ~/.vault
#   age-keygen -o ~/.vault/key.txt
#   echo '{}' | age -r <public-key> -o ~/.vault/secrets.age

VAULT_DIR="$HOME/.vault"
VAULT_FILE="$VAULT_DIR/secrets.age"
KEY_FILE="$VAULT_DIR/key.txt"

decrypt() {
    if [ ! -f "$VAULT_FILE" ]; then
        echo "{}"
        return
    fi
    age --decrypt -i "$KEY_FILE" "$VAULT_FILE"
}

encrypt() {
    local pubkey
    pubkey=$(grep "public key:" "$KEY_FILE" | awk '{print $NF}')
    age -r "$pubkey" -o "$VAULT_FILE"
}

case "$1" in
    get)
        decrypt | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('$2',''))"
        ;;
    set)
        tmp=$(mktemp)
        decrypt > "$tmp"
        # Read value from stdin (not CLI args — prevents /proc/cmdline leaks)
        value=$(cat)
        python3 -c "
import sys, json
with open(sys.argv[1]) as f:
    d = json.load(f)
d[sys.argv[2]] = sys.argv[3]
with open(sys.argv[1], 'w') as f:
    json.dump(d, f, indent=2)
" "$tmp" "$2" "$value"
        encrypt < "$tmp"
        rm "$tmp"
        echo "Set $2"
        ;;
    list)
        decrypt | python3 -c "import sys,json; [print(k) for k in json.load(sys.stdin).keys()]"
        ;;
    edit)
        tmp=$(mktemp --suffix=.json)
        decrypt > "$tmp"
        ${EDITOR:-nano} "$tmp"
        encrypt < "$tmp"
        rm "$tmp"
        echo "Vault saved"
        ;;
    *)
        echo "Usage: vault.sh get <key> | set <key> (value via stdin) | list | edit"
        exit 1
        ;;
esac
