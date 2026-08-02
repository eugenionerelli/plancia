#!/bin/bash
# Un certificato di firma stabile, per non ridare i permessi a ogni build.
#
#   ./tools/certificato.sh
#
# Il problema che risolve: macOS lega il consenso al microfono e alla dettatura
# all'identità con cui l'app è firmata. Con la firma ad hoc quell'identità è
# l'impronta del binario, che cambia a ogni compilazione: ricompili, e Jarvis
# riparte muto chiedendo di nuovo i permessi.
#
# Questo script crea un certificato auto firmato chiamato "Plancia" nel tuo
# portachiavi. `mac/build.sh` lo trova da solo e lo usa. Il consenso allora resta
# dato fra una build e l'altra.
#
# Non serve per distribuire: per quello ci vuole un Developer ID Apple, e questo
# certificato non lo sostituisce. Serve solo sulla tua macchina.
#
# Il sistema può chiederti la password del portachiavi una volta.

set -euo pipefail

NOME="Plancia"
GIORNI=3650
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

if security find-identity -v -p codesigning | grep -q "\"$NOME\""; then
  echo "c'è già un certificato di firma chiamato $NOME, non tocco niente"
  exit 0
fi

echo "==> genero chiave e certificato"
cat > "$TMP/cert.cnf" <<'CNF'
[ req ]
distinguished_name = dn
x509_extensions = v3
prompt = no

[ dn ]
CN = Plancia

[ v3 ]
basicConstraints = critical,CA:false
keyUsage = critical,digitalSignature
extendedKeyUsage = critical,codeSigning
1.2.840.113635.100.6.1.13 = critical,DER:0500
CNF

openssl req -x509 -newkey rsa:2048 -nodes -days "$GIORNI" \
  -keyout "$TMP/chiave.pem" -out "$TMP/cert.pem" -config "$TMP/cert.cnf" 2>/dev/null

# Gli algoritmi vecchi non sono una svista: il portachiavi di macOS non legge il
# PKCS12 che OpenSSL 3 produce di default, e fallisce dicendo "wrong password".
openssl pkcs12 -export -inkey "$TMP/chiave.pem" -in "$TMP/cert.pem" \
  -out "$TMP/plancia.p12" -passout pass:plancia -name "$NOME" \
  -keypbe PBE-SHA1-3DES -certpbe PBE-SHA1-3DES -macalg sha1 2>/dev/null

echo "==> lo metto nel portachiavi (può chiederti la password)"
security import "$TMP/plancia.p12" -k ~/Library/Keychains/login.keychain-db \
  -P plancia -A -T /usr/bin/codesign -T /usr/bin/security >/dev/null

# Senza questo, codesign chiede il permesso di usare la chiave a ogni firma.
security set-key-partition-list -S apple-tool:,apple:,codesign: \
  -k "" ~/Library/Keychains/login.keychain-db >/dev/null 2>&1 || \
  echo "  (non sono riuscito a sbloccare la chiave: codesign potrebbe chiedertelo ogni volta)"

echo "==> mi fido di lui per la firma del codice"
security add-trusted-cert -p codeSign -k ~/Library/Keychains/login.keychain-db \
  "$TMP/cert.pem" 2>/dev/null || \
  echo "  (non sono riuscito a impostare la fiducia: aprila da Accesso Portachiavi,"
  echo "   doppio clic su $NOME, Fiducia, Firma codice: Consenti sempre)"

echo
if security find-identity -v -p codesigning | grep -q "\"$NOME\""; then
  echo "fatto. Ora ricompila una volta sola:"
  echo "  ./mac/build.sh --install"
  echo "Dai i permessi quando li chiede: da lì in poi restano."
else
  echo "il certificato non risulta ancora utilizzabile per firmare."
  echo "Aprilo da Accesso Portachiavi e metti Firma codice su Consenti sempre."
fi
