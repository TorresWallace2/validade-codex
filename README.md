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
3. Execute a aplicação:\r
   ```bash
   python app.py
   ```
4. Acesse em [http://localhost:5000](http://localhost:5000).

O banco SQLite é criado automaticamente em `instance/documents.db` na primeira execução.
