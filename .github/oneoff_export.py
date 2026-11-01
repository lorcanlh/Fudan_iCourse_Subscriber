"""Export classroom text only inside an authenticated encrypted envelope."""
import io
import json
import os
import sqlite3
import zipfile
from pathlib import Path

from Crypto.Cipher import AES, PKCS1_OAEP
from Crypto.Hash import SHA256
from Crypto.PublicKey import RSA


def export(db_path, public_path, output_path):
    with sqlite3.connect(Path(db_path).resolve().as_uri() + '?mode=ro', uri=True) as db:
        db.row_factory = sqlite3.Row
        rows = [dict(r) for r in db.execute('''SELECT l.*, c.title AS course_title, c.teacher
            FROM lectures l LEFT JOIN courses c USING(course_id)
            ORDER BY l.date, l.course_id, l.sub_id''')]
    # Include inventory so the receiving process can report skipped/missing data;
    # both inventory and original classroom text stay inside encryption.
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, 'w', zipfile.ZIP_DEFLATED) as archive:
        archive.writestr('lectures.json', json.dumps(rows, ensure_ascii=False))
    key = os.urandom(32)
    aes = AES.new(key, AES.MODE_GCM)
    ciphertext, tag = aes.encrypt_and_digest(buffer.getvalue())
    rsa = RSA.import_key(Path(public_path).read_bytes())
    wrapped = PKCS1_OAEP.new(rsa, hashAlgo=SHA256).encrypt(key)
    import base64
    envelope = {k: base64.b64encode(v).decode() for k, v in
                {'key': wrapped, 'nonce': aes.nonce, 'tag': tag, 'ciphertext': ciphertext}.items()}
    envelope['version'] = 1
    Path(output_path).write_text(json.dumps(envelope))
    print('Encrypted course-text export prepared; no plaintext artifact created.')


if __name__ == '__main__':
    export('data/icourse.db', '.github/oneoff-public.pem', 'course-text.enc')
