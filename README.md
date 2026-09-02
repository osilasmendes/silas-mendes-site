# Silas Mendes Desenvolvedor

Site/portal profissional com portfólio, cadastro de clientes, solicitação de atendimento, orçamento para sites e sistemas e painel administrativo.

## Recursos incluídos

- Página inicial profissional e responsiva
- Portfólio de projetos
- Cadastro e login de clientes
- Área do cliente
- Solicitação de atendimento
- Envio do pedido para WhatsApp
- Solicitação de orçamento para sites e sistemas
- Histórico de solicitações do cliente
- Painel administrativo
- Alteração de status de atendimentos e orçamentos
- Cadastro de novos usuários pelo administrador
- Bloqueio e desbloqueio de usuários
- Senhas armazenadas com hash
- Banco SQLite para desenvolvimento local

## Como abrir no VSCode

1. Extraia a pasta do projeto.
2. Abra a pasta `silas_mendes_site` no VSCode.
3. Abra o terminal integrado.
4. Crie um ambiente virtual:

### Windows

```bash
python -m venv .venv
.venv\Scripts\activate
```

5. Instale as dependências:

```bash
pip install -r requirements.txt
```

6. Copie `.env.example` para um arquivo chamado `.env`.
7. No `.env`, altere principalmente `SECRET_KEY`, `ADMIN_EMAIL` e `ADMIN_PASSWORD`.
8. Inicie:

```bash
python app.py
```

9. Abra no navegador:

```text
http://127.0.0.1:5000
```

## Administrador

Na primeira inicialização, a aplicação cria automaticamente a conta administrativa com os dados definidos no `.env`.

## Banco de dados

Nesta versão inicial é usado SQLite, salvo dentro da pasta `instance/`. Isso é ideal para desenvolvimento local. Quando o layout e as funções estiverem finalizados, o projeto pode ser migrado para PostgreSQL/Neon e hospedado.

## WhatsApp

O número usado para receber pedidos fica no `.env`:

```env
WHATSAPP_NUMBER=5581998925005
```

Utilize DDI + DDD + número, apenas números.

## Próximas edições sugeridas

- Adicionar imagens reais dos projetos
- Criar página individual para cada projeto
- Cadastrar serviços e preços pelo painel administrativo
- Adicionar recuperação de senha
- Adicionar edição de perfil
- Integrar PostgreSQL/Neon
- Preparar deploy e domínio
