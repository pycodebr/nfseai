"""Registro local das emissões: uma linha por referência, controle principal contra nota em dobro.

O portal não tem idempotência: duas chamadas de emissão geram duas notas. O registro
guarda, para cada empresa e referência (o identificador da venda ou do contrato no
sistema de quem emite), em que ponto a emissão está. Uma referência registrada como
``emitida``, ``incerta``, ``preparada`` ou ``cancelada`` não é emitida de novo sem uma
decisão explícita.
"""
import secrets
import sqlite3
from contextlib import closing
from datetime import datetime
from pathlib import Path

STATUS_PREPARADA = 'preparada'
STATUS_EMITIDA = 'emitida'
STATUS_INCERTA = 'incerta'
STATUS_ERRO = 'erro'
STATUS_CANCELADA = 'cancelada'
BLOCKING_STATUSES = (STATUS_PREPARADA, STATUS_EMITIDA, STATUS_INCERTA, STATUS_CANCELADA)

SCHEMA = """
CREATE TABLE IF NOT EXISTS emissoes (
    empresa TEXT NOT NULL,
    referencia TEXT NOT NULL,
    tomador_documento TEXT NOT NULL,
    tomador_nome TEXT,
    valor TEXT NOT NULL,
    competencia TEXT NOT NULL,
    status TEXT NOT NULL,
    draft_id TEXT,
    chave_acesso TEXT,
    numero_nfse TEXT,
    numero_dps TEXT,
    serie_dps TEXT,
    emitted_at TEXT,
    pdf_path TEXT,
    detalhe TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (empresa, referencia)
);
CREATE UNIQUE INDEX IF NOT EXISTS emissoes_chave ON emissoes (chave_acesso) WHERE chave_acesso IS NOT NULL;
CREATE TABLE IF NOT EXISTS historico (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    at TEXT NOT NULL,
    empresa TEXT NOT NULL,
    referencia TEXT NOT NULL,
    evento TEXT NOT NULL,
    chave_acesso TEXT,
    detalhe TEXT
);
"""
UPDATABLE = frozenset({
    'status', 'draft_id', 'chave_acesso', 'numero_nfse', 'numero_dps', 'serie_dps', 'tomador_nome',
    'emitted_at', 'pdf_path', 'detalhe', 'valor', 'competencia', 'tomador_documento',
})


def _now():
    return datetime.now().isoformat(timespec='seconds')


def auto_reference():
    """Referência gerada quando quem emite não informa uma (sem proteção entre execuções)."""
    return f'auto-{datetime.now():%Y%m%d-%H%M%S}-{secrets.token_hex(3)}'


class Ledger:
    def __init__(self, path):
        self._path = Path(path)
        self._path.parent.mkdir(parents=True, exist_ok=True)
        with closing(self._connect()) as conn, conn:
            conn.executescript(SCHEMA)
        self._path.chmod(0o600)

    def _connect(self):
        conn = sqlite3.connect(self._path)
        conn.row_factory = sqlite3.Row
        return conn

    def get(self, empresa, referencia):
        with closing(self._connect()) as conn:
            row = conn.execute(
                'SELECT * FROM emissoes WHERE empresa = ? AND referencia = ?', (empresa, referencia)
            ).fetchone()
        return dict(row) if row else None

    def by_chave(self, chave_acesso):
        with closing(self._connect()) as conn:
            row = conn.execute('SELECT * FROM emissoes WHERE chave_acesso = ?', (chave_acesso,)).fetchone()
        return dict(row) if row else None

    def list(self, empresa=None, *, status=None, competencia=None, limit=50):
        clauses, params = [], []
        for column, value in (('empresa', empresa), ('status', status), ('competencia', competencia)):
            if value:
                clauses.append(f'{column} = ?')
                params.append(value)
        where = f'WHERE {" AND ".join(clauses)}' if clauses else ''
        with closing(self._connect()) as conn:
            rows = conn.execute(
                f'SELECT * FROM emissoes {where} ORDER BY updated_at DESC LIMIT ?',  # noqa: S608
                (*params, int(limit)),
            ).fetchall()
        return [dict(row) for row in rows]

    def start(self, *, empresa, referencia, tomador_documento, valor, competencia, draft_id, tomador_nome=None):
        """Registra a referência como preparada antes do envio da emissão."""
        now = _now()
        with closing(self._connect()) as conn, conn:
            conn.execute(
                """INSERT INTO emissoes (empresa, referencia, tomador_documento, tomador_nome, valor, competencia,
                       status, draft_id, created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                   ON CONFLICT (empresa, referencia) DO UPDATE SET
                       tomador_documento = excluded.tomador_documento, tomador_nome = excluded.tomador_nome,
                       valor = excluded.valor, competencia = excluded.competencia, status = excluded.status,
                       draft_id = excluded.draft_id, chave_acesso = NULL, numero_nfse = NULL, numero_dps = NULL,
                       serie_dps = NULL, emitted_at = NULL, pdf_path = NULL, detalhe = NULL,
                       updated_at = excluded.updated_at""",
                (
                    empresa, referencia, tomador_documento, tomador_nome, str(valor),
                    competencia.isoformat() if hasattr(competencia, 'isoformat') else str(competencia),
                    STATUS_PREPARADA, draft_id, now, now,
                ),
            )

    def update(self, empresa, referencia, **fields):
        unknown = set(fields) - UPDATABLE
        if unknown:
            raise ValueError(f'campos desconhecidos no registro: {sorted(unknown)}')
        fields['updated_at'] = _now()
        assignments = ', '.join(f'{name} = ?' for name in fields)
        with closing(self._connect()) as conn, conn:
            cursor = conn.execute(
                f'UPDATE emissoes SET {assignments} WHERE empresa = ? AND referencia = ?',  # noqa: S608
                (*fields.values(), empresa, referencia),
            )
            if cursor.rowcount != 1:
                raise KeyError(f'referência {empresa}/{referencia} não está no registro')

    def delete(self, empresa, referencia):
        with closing(self._connect()) as conn, conn:
            cursor = conn.execute(
                'DELETE FROM emissoes WHERE empresa = ? AND referencia = ?', (empresa, referencia)
            )
        return cursor.rowcount == 1

    def log(self, empresa, referencia, evento, chave_acesso=None, detalhe=None):
        """Histórico só de acréscimo: emissões, cancelamentos e ajustes de cada referência."""
        with closing(self._connect()) as conn, conn:
            conn.execute(
                'INSERT INTO historico (at, empresa, referencia, evento, chave_acesso, detalhe) '
                'VALUES (?, ?, ?, ?, ?, ?)',
                (_now(), empresa, referencia, evento, chave_acesso, detalhe),
            )

    def history(self, empresa, referencia):
        with closing(self._connect()) as conn:
            rows = conn.execute(
                'SELECT at, evento, chave_acesso, detalhe FROM historico '
                'WHERE empresa = ? AND referencia = ? ORDER BY id',
                (empresa, referencia),
            ).fetchall()
        return [dict(row) for row in rows]
