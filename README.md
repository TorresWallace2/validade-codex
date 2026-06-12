# Gestor de Documentos

Aplicação web completa para gestão e controle de documentos com Flask e Bootstrap.

## Recursos principais

- Navegação de pastas e arquivos com ordenação natural, ícones por tipo e ordenação por colunas.
- Controle de validade com status automático (OK, A vencer, Vencido, Indeterminada, Não definido).
- Painel lateral com detalhes, observações e ações rápidas (abrir, renomear, excluir, definir validade).
- Upload de arquivos, criação/remoção de pastas e arquivos, exportação CSV.
- Presets de pastas favoritas e configuração de alerta global.
- Interface responsiva com Bootstrap 5, dark mode, busca, filtros de status, infinit scroll e toasts.

## Como executar

1. Crie um ambiente virtual (opcional, mas recomendado):
   ```bash
   python -m venv .venv
   .\.venv\Scripts\activate
   ```
2. Instale as dependências:
   ```bash
   pip install -r requirements.txt
   ```
3. Crie o arquivo `.env` a partir do exemplo e preencha as credenciais quando for usar Google Drive:
   ```bash
   copy .env.example .env
   ```
   Configure no `.env`:
   ```env
   GOOGLE_CLIENT_ID=seu-client-id
   GOOGLE_CLIENT_SECRET=seu-client-secret
   GOOGLE_REDIRECT_URI=http://localhost:5000/auth/google/callback
   DATABASE_URL=postgresql://user:password@host:5432/database
   ```
   `DATABASE_URL` e obrigatoria no Render para persistir as contas conectadas do Google Drive entre restarts e novos deploys.
4. Execute a aplicação:
   ```bash
   python app.py
   ```
5. Acesse em [http://localhost:5000](http://localhost:5000).

O banco SQLite e criado automaticamente em `instance/documents.db` na primeira execucao para os dados locais do app. As contas conectadas do Google Drive passam a ser persistidas no Postgres configurado em `DATABASE_URL`.
