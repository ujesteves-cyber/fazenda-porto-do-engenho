# Manter usuários logados (sessão persistente)

**Status:** Aprovado
**Data:** 2026-05-05
**Branch:** `claude/musing-cray-471328`

## Problema

Hoje a sessão Flask é um cookie de navegador (`session cookie`): expira quando o usuário fecha o navegador. Toda vez que volta ao sistema, precisa reautenticar. Pra um sistema usado todo dia em equipamento próprio, isso é fricção desnecessária.

## Decisão

Sessão persistente de **365 dias** ativada para todo usuário automaticamente após login (sem checkbox "lembrar de mim"). Usuário continua logado mesmo após fechar/reiniciar o navegador, até clicar em "Sair" ou ficar 365 dias sem acessar.

Implementação via `session.permanent = True` + `app.permanent_session_lifetime = timedelta(days=365)`. Nenhuma tabela nova; reutiliza o cookie de sessão Flask já assinado com `SECRET_KEY`.

## Mudanças

### `app.py` — config global (logo após `app.secret_key = ...`)

```python
from datetime import timedelta  # já importado no topo

app.permanent_session_lifetime = timedelta(days=365)
app.config.update(
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE='Lax',
    SESSION_COOKIE_SECURE=os.getenv('FLASK_ENV') != 'development',
)

if app.secret_key == 'dev-secret-key-change-me' and os.getenv('FLASK_ENV') != 'development':
    raise RuntimeError(
        'SECRET_KEY precisa ser configurado em produção. '
        'Gere com: python -c "import secrets; print(secrets.token_hex(32))"'
    )
```

### `app.py` — função `login()` (linha ~232)

Marcar a sessão como permanente antes de gravar `user_id`:

```python
if user and bcrypt.checkpw(senha, user['senha_hash'].encode()):
    session.permanent = True            # ← novo
    session['user_id'] = user['id']
    session['user_nome'] = user['nome']
    next_url = request.args.get('next', '/')
    return redirect(next_url)
```

### `app.py` — `current_user()` (linha ~179): filtrar por `ativo`

Pré-existente: `current_user()` não checa `ativo`, então um usuário desativado com sessão válida continua autenticado. Hoje passa quase despercebido (sessão morre ao fechar navegador). Com sessão de 365 dias, vira buraco real — desativar não tem efeito enquanto o cookie viver. Adicionar `AND ativo=1` à query:

```python
return get_db().execute(
    "SELECT id, nome, email, papel FROM usuarios WHERE id=? AND ativo=1",
    (session['user_id'],)
).fetchone()
```

Efeito: usuário desativado vira "sem sessão" no próximo request → cai no `login_required` → redireciona pra tela de login. Funciona igual a logout forçado.

### `.env.example` — documentar `SECRET_KEY`

Adicionar comentário explicando como gerar e a obrigatoriedade em produção.

## Comportamento

| Cenário | Resultado |
|---|---|
| Faz login | Cookie válido por 365 dias |
| Fecha navegador / reinicia computador | Continua logado |
| Clica em "Sair" (`/logout`) | `session.clear()` → desloga imediatamente |
| 365 dias sem acessar | Cookie expira → tem que logar de novo |
| Master desativa o usuário (`ativo=0`) | No próximo request `current_user()` retorna `None` → cai em `login_required` → redirect pra login (depende do fix em `current_user()` listado abaixo) |
| Usuário muda a senha (futuro) | Sessões antigas continuam válidas (limitação aceita; fora do escopo) |

## O que NÃO muda

- `templates/login.html` — formulário continua igual
- Decoradores `login_required` / `api_login_required` — sem mudança
- Schema do banco — sem mudança
- Rotas — sem mudança

## Pré-requisito de deploy

`SECRET_KEY` precisa estar setado no Render com valor forte (32+ bytes aleatórios). O fail-fast acima impede o app de subir em produção sem a variável configurada — o gunicorn vai falhar no boot, deixando claro que falta a config.

## Riscos e mitigações

| Risco | Mitigação |
|---|---|
| `SECRET_KEY` fraco/default → forja de cookies → acesso por 365 dias | Fail-fast em produção; documentação no `.env.example` |
| Cookie roubado em rede insegura | `SESSION_COOKIE_SECURE=True` em produção (só transita em HTTPS) |
| Cookie roubado via XSS | `SESSION_COOKIE_HTTPONLY=True` (JS não acessa o cookie) |
| Cookie usado em request cross-site | `SESSION_COOKIE_SAMESITE='Lax'` |
| Computador compartilhado: usuário esquece de deslogar | Aceito — é trade-off explícito da escolha pelo modelo "sempre persistente". Botão "Sair" continua disponível. |

## Fora de escopo

- Checkbox "Lembrar de mim" (consideramos e descartamos — usuário escolheu opção A: sempre persistente)
- Auto-revogação de sessões ao trocar senha
- Tabela de tokens server-side com revogação granular
- Rotação de `SECRET_KEY`
