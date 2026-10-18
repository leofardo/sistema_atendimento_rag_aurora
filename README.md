# Chatbot de atendimento com IA — Aurora Casa & Tech

Assistente virtual ("Lia") de uma varejista **fictícia**, com RAG sobre as políticas da empresa e as leis federais de atendimento, custo de cada mensagem em tokens, dólar e real, e um conjunto de técnicas de economia de token.

- **backend/**: API FastAPI (RAG, roteamento, memória, livro-caixa de custos)
- **frontend/**: Streamlit (Atendimento, Custos e tokens, Base de conhecimento)

## Como rodar

```powershell
# 1. Ambiente (uma vez, na raiz do projeto)
py -3.12 -m venv .venv
.venv\Scripts\pip install -r backend\requirements.txt -r frontend\requirements.txt

# 2. Chave da OpenAI: edite backend\.env e preencha OPENAI_API_KEY

# 3. Indexar a base (uma vez; custa ~R$ 0,002)
cd backend
..\.venv\Scripts\python -m scripts.indexar

# 4. API (terminal 1, dentro de backend\)
..\.venv\Scripts\uvicorn app.main:app --reload

# 5. Interface (terminal 2, dentro de frontend\)
..\.venv\Scripts\streamlit run app.py
```

Abra http://127.0.0.1:8501. As leis já estão baixadas em `backend/dados/documentos/`. Para baixar de novo do Planalto: `python -m scripts.baixar_documentos`.

## Arquitetura

```
Streamlit (8501) ──HTTP/NDJSON──▶ FastAPI (8000)
                                    ├─ servicos/atendente.py   fluxo do turno (do mais barato ao mais caro)
                                    ├─ servicos/regras.py      respostas prontas (R$ 0)
                                    ├─ servicos/roteador.py    gpt-4.1-nano: intenção + consulta reescrita + extração
                                    ├─ servicos/prazos.py      prazos calculados em Python
                                    ├─ rag/documentos.py       chunking estrutural
                                    ├─ rag/indice.py           ChromaDB + BM25 + RRF
                                    ├─ rag/cache_semantico.py  reaproveita respostas parecidas
                                    ├─ repositorios/memoria.py conversa em SQLite (ou Redis)
                                    └─ repositorios/custos.py  livro-caixa (SQLite)
```

**Por que FastAPI + Streamlit:** a separação faz sentido porque a inteligência (RAG, custos, memória) fica numa API que qualquer canal pode usar: o Streamlit hoje, um widget no site ou um WhatsApp amanhã. O Streamlit é ótimo para o painel interno de chat e custos, mas não seria a interface final para o cliente. A resposta sai em streaming (NDJSON, um evento JSON por linha), e o último evento traz custos, rota e fontes.

### O fluxo de uma mensagem

| Passo | O que faz | Custo típico |
|---|---|---|
| 1. Regra | "oi", "obrigado", menu, horário, "quero um atendente", "pedido 100231" | **R$ 0** |
| 2. Roteador (`gpt-4.1-nano`) | intenção, se precisa da base, **consulta reescrita**, nº do pedido, data, durável? | ~R$ 0,0004 |
| 3. Dados do sistema | pedido no banco + prazos (arrependimento, garantia) calculados em Python | R$ 0 |
| 4. Embedding (`text-embedding-3-small`) | vetor da consulta → cache semântico → busca híbrida | ~R$ 0,000002 |
| 5. Resposta (`gpt-4.1-mini`, streaming) | prompt fixo (em cache) + trechos + pergunta | ~R$ 0,003–0,005 |

Custos **medidos** no teste real: mensagem com RAG **R$ 0,005–0,008**; pergunta repetida (cache semântico) R$ 0,0004; transferência para humano e fora do escopo R$ 0,0009 (só o roteador); saudação e status de pedido R$ 0.

**Cache de prefixo da OpenAI:** o prompt fixo é elegível (> 1.024 tokens) e as chamadas levam `prompt_cache_key`, mas o desconto é "melhor esforço". Com as mesmas chamadas, o teste deu ora 0, ora 1.280 tokens em cache. Com mais tráfego, a taxa de acerto sobe.

## RAG e embeddings: ferramentas e táticas

### 1. Base de conhecimento (curadoria)
- **Leis federais** baixadas do Planalto (`scripts/baixar_documentos.py`): CDC (Lei 8.078/1990, só o Título I, arts. 1–60), Decreto 11.034/2022 (SAC), Decreto 7.962/2013 (comércio eletrônico) e Lei 10.048/2000 (atendimento prioritário).
- **Manual da Aurora** (`aurora-manual-de-atendimento.md`), escrito de acordo com essas leis.
- **Limpeza antes de indexar:** remove o texto revogado (`<strike>`), anotações como "(Redação dada pela Lei nº ...)" e dispositivos vetados. Cada token removido aqui deixa de ser pago em toda pergunta que recuperar aquele trecho.
- **Curadoria:** o CDC vai só até o art. 60. Crime e processo judicial não são assunto de atendimento e só disputariam espaço na busca.

### 2. Chunking estrutural (`rag/documentos.py`)
O curso da Alura compara o corte por caracteres, por tokens e o semântico (`SemanticChunker`). Para lei e política existe um corte melhor: **a própria estrutura do texto**.
- Lei: **1 trecho por artigo**, com seus §§ e incisos. Artigo é unidade de sentido e é o que se cita.
- Manual: **1 trecho por seção** `##`.
- Artigo longo (> 450 tokens): subdividido com `RecursiveCharacterTextSplitter.from_tiktoken_encoder`, a mesma ferramenta do curso, com 60 tokens de sobreposição.
- **Cabeçalho de contexto:** cada trecho é indexado como `CDC > Cap. VI — Da Proteção Contratual > Seção I > Art. 49` + texto. Sem isso, o art. 49 sozinho não diz que é sobre contrato.
- Resultado: 118 trechos, ~22 mil tokens no total.

### 3. Embedding: `text-embedding-3-small`
- US$ 0,02 por milhão de tokens (6,5× mais barato que o `-large`), multilíngue e bom em português.
- A base inteira custa ~R$ 0,002 para indexar, e cada pergunta ~R$ 0,000002.
- **Indexação incremental:** o id do trecho é o hash do conteúdo. Reindexar sem mudar documento custa zero; mudou um artigo, só ele paga.
- **Lote:** até 128 textos por requisição (mesmo preço, muito mais rápido).
- `OPENAI_DIMENSOES_EMBEDDING`: o modelo aceita vetores menores (512, 256). Isso reduz disco e acelera a busca, mas **não muda o preço**, que é por token de entrada.

### 4. Banco vetorial: ChromaDB
- Persistente em disco (`dados/indice/chroma`), índice HNSW com distância de **cosseno**.
- Usamos `embedding_function=None`: os vetores vêm da OpenAI, e o Chroma não baixa modelo local nenhum.
- Por que não o Milvus do curso: o Milvus Lite não roda no Windows. O FAISS funcionaria, mas o Chroma guarda metadados e documento junto com o vetor.

### 5. Busca híbrida: vetorial + BM25 + RRF + artigo citado (`rag/indice.py`)
- **Vetorial** entende sentido ("me arrependi" → art. 49), mas é fraca em termo exato.
- **BM25** (`rank_bm25`) acerta termo exato: "art. 49", "Decreto 11.034", "Aurora+", "0800". O texto é normalizado sem acento e sem stopwords.
- **Reciprocal Rank Fusion ponderado:** cada trecho ganha `peso/(60 + posição)` em cada lista. Com **peso igual, o BM25 piorou a busca** (de 96% para 89% de acerto no top-4), porque o embedding já acerta quase tudo sozinho. Medido com `avaliar_rag.py --pesos`, o BM25 ficou com peso **0,2** (`RAG_PESO_BM25`): só desempata.
- **Artigo citado pelo número vai direto:** "o que diz o artigo 10 do decreto 11.034?" nem aparecia entre os 20 candidatos da busca vetorial. Agora o código reconhece a citação e busca o trecho pelos metadados (dica 2: o código faz).
- **Filtro de relevância** (`RAG_SIMILARIDADE_MINIMA=0,40`): no teste real, o pior trecho certo teve similaridade 0,47 e perguntas fora do assunto chegaram a 0,35. Se nada passar, a resposta é uma frase pronta oferecendo atendente, **sem chamar o LLM**.
- **top-k = 4**: cada trecho custa de 150 a 400 tokens em toda resposta.

### 6. Query rewriting no lugar de HyDE/Multi-Query
O curso usa **HyDE** (gerar uma resposta hipotética e buscar por ela) e **Multi-Query** (5 versões da pergunta). As duas técnicas melhoram a busca, mas custam uma chamada de LLM a mais por pergunta, e a Multi-Query ainda faz 5 buscas.

Aqui, o **roteador**, que já roda para classificar a intenção, também reescreve a pergunta como uma consulta autônoma no vocabulário dos documentos. "E se já passou?" vira "prazo de arrependimento vencido compra online". É o "condense question" do LangChain e resolve as perguntas de continuação **sem custo extra**.

### 7. Cache semântico (`rag/cache_semantico.py`)
- O vetor da mensagem sai na mesma requisição de embedding da busca. Se ele tiver similaridade ≥ 0,95 com uma pergunta já respondida, a resposta sai do cache e a chamada mais cara do turno não acontece. No teste real, uma pergunta parafraseada saiu por **R$ 0,00038**, contra R$ 0,0059 pelo RAG.
- Só entram **primeiras perguntas da conversa**, genéricas (política, lei), sem pedido nem data. A chave é a **mensagem original**, não a consulta reescrita. No teste real, uma resposta que citava a "air fryer" dita pelo cliente foi servida a outro cliente que não falou de air fryer.
- O cache é zerado quando a base muda, e cada resposta vale por 7 dias.

### 8. Avaliação (`scripts/avaliar_rag.py`)
Mesma ideia dos `qa_pairs` + `QAEvalChain` do curso, com o gabarito da busca:
- `avaliacao/perguntas.json`: 28 perguntas, cada uma com os trechos que **deveriam** vir e a resposta esperada.
- **acerto@k e MRR** comparando os modos só vetorial, só BM25 e híbrido. Custa ~R$ 0,0001.
- Mostra a similaridade do trecho certo contra a do melhor trecho errado, que é o número para **calibrar o limiar**.
- `--juiz`: gera as respostas e um nano dá a nota (CORRETA/PARCIAL/INCORRETA).

Rode depois de cada mudança de prompt, chunking ou limiar.

**Resultado atual** (31 perguntas, com o roteador reescrevendo):

| Modo | acerto@1 | acerto@4 | MRR |
|---|---|---|---|
| só vetorial | 87% | 94% | 0,900 |
| só BM25 | 74% | 90% | 0,818 |
| **atendimento (fusão 0,2 + artigo citado + limiar)** | 87% | **100%** | 0,914 |

Juiz: **27 corretas, 4 parciais, 0 incorretas**. Custo da avaliação completa: R$ 0,17.

## Fine-tuning (`scripts/finetuning_roteador.py`)

**Destilação** do roteador: um professor (`gpt-4.1-mini` com o prompt completo de 1.095 tokens) rotula ~100 mensagens de `dados/finetuning/mensagens_semente.txt`. Um aluno (`gpt-4.1-nano`) é treinado para fazer o mesmo com um prompt de **23 tokens**.

```powershell
python -m scripts.finetuning_roteador gerar     # ~R$ 0,30 (professor)
python -m scripts.finetuning_roteador validar   # estimativa do treino: ~R$ 0,22
python -m scripts.finetuning_roteador enviar    # cria o job (cobra o treino)
python -m scripts.finetuning_roteador status    # no fim: OPENAI_MODELO_ROTEADOR=ft:...
python -m scripts.finetuning_roteador comparar  # acerto e custo: base x afinado
```

Fine-tuning ensina **comportamento e formato**, não conhecimento. As políticas continuam no RAG: se a política mudar, basta reindexar, sem retreinar.

**Quando compensa:** o token do modelo afinado custa 2× o do base, e o prompt longo já sai com 75% de desconto pelo cache. A economia em dinheiro é pequena. Os ganhos reais são consistência na classificação, menos latência e um prompt que não precisa crescer a cada regra nova. O `comparar` mede tudo isso.

> ⚠️ A OpenAI está encerrando a plataforma de fine-tuning. Contas novas já não têm acesso, e contas antigas ainda podem criar jobs por alguns meses. Se `enviar` der erro de permissão, é isso. O resto do projeto não depende do fine-tuning.

## Economia de token: o que foi aplicado

Mapa do `detalhes ia economia token.txt` para o código:

| Dica | Onde |
|---|---|
| 1. Buscar só o necessário | RAG top-4 com filtro de relevância; pedido buscado por número |
| 2. Código faz a conta | `servicos/prazos.py` (7 dias, 30/90 dias, troca direta); atraso de entrega |
| 3. Prompt caching | `prompt_resposta.SISTEMA` (1.331 tokens) e roteador (1.095), fixos no system; o variável vai no fim |
| 4. Formato compacto | pedido em `campo=valor`; trechos só com caminho + texto; histórico em texto corrido |
| 5. Sem dado duplicado | fontes vão para a tela, não para o prompt |
| 6. Histórico curto | últimas 6 mensagens, respostas antigas cortadas em 500 caracteres, resumo (nano) do que sai da janela |
| 7. Regra antes da IA | `servicos/regras.py` |
| 8. Modelo certo por tarefa | nano no roteador e no resumo; mini só na resposta |
| 9. Tamanho da resposta | `max_completion_tokens=450`, prompt pedindo 2–4 frases |
| 10. Sem idas e voltas | "responda com a interpretação mais provável" |
| 13. Resposta pequena sem dado | nada relevante → frase pronta, sem LLM |
| 14. Medir tudo | `ai_usage` + `turnos`; tokens vindos do `usage` da API; tela de Custos |
| Extras | cache semântico, RAG, resumo de conversa, transferência para humano cedo, avaliação |

## API REST

Documentação interativa em http://127.0.0.1:8000/docs. Para testar, clique em **Authorize** e cole a senha (`AUTH_SENHA` do `backend/.env`). Todas as rotas ficam sob `/api/v1` e exigem a senha no cabeçalho `X-Senha`; só `/health` é pública.

| Método | Rota | O que faz |
|---|---|---|
| POST | `/api/v1/conversas` | cria conversa e protocolo (201) |
| GET | `/api/v1/conversas` | lista conversas, com custo de cada uma |
| GET | `/api/v1/conversas/{id}` | abre a conversa com custos, rota e fontes (404 se não existir) |
| DELETE | `/api/v1/conversas/{id}` | apaga a conversa (204); os custos ficam no livro-caixa |
| POST | `/api/v1/conversas/{id}/mensagens` | envia mensagem; resposta em streaming NDJSON |
| GET | `/api/v1/base/documentos` | documentos do RAG, com trechos e tokens |
| GET | `/api/v1/base/trechos?fonte=` / `?busca=` | lista trechos ou mostra o que a busca híbrida devolve |
| POST | `/api/v1/base/indexacoes` | reindexa (incremental) e limpa o cache se a base mudou |
| GET / DELETE | `/api/v1/base/cache` | lista / limpa o cache semântico |
| DELETE | `/api/v1/base/cache/{id}` | apaga uma resposta do cache (204) |
| GET | `/api/v1/pedidos`, `/api/v1/pedidos/{numero}` | pedidos fictícios |
| GET | `/api/v1/custos/resumo`, `/chamadas`, `/mensagens` | livro-caixa |
| POST | `/api/v1/custos/estimativas` | tokens e custo de um texto como entrada |
| GET | `/api/v1/configuracao` | modelos, preços e parâmetros em uso |

## Memória da conversa: SQLite ou Redis?

O padrão é **SQLite**, e com `REDIS_URL` no `.env` a memória vai para o **Redis**. As duas implementações têm a mesma interface (`repositorios/memoria.py`).

- **Hoje, SQLite basta:** um servidor só, nada para instalar, e a conversa sobrevive a reinício. Esta máquina não tem Redis, Docker nem WSL.
- **O Redis passa a valer** com mais de uma instância da API atrás de um balanceador (todas leem a mesma memória), com expiração automática de conversa (TTL) ou, no futuro, para o cache semântico (Redis Stack tem busca vetorial). A implementação usa `RPUSH` + `LTRIM`: o Redis guarda só a janela que o prompt usa.
- **O livro-caixa fica no SQLite de qualquer jeito:** custo precisa durar e ser somado por período e intenção, o que é trabalho de SQL.

## Pedidos fictícios para testar

`100231` (air fryer entregue há 4 dias, dentro do arrependimento) · `100245` (TV saindo para entrega) · `100250` (em separação) · `100262` (geladeira entregue há 35 dias, fora do arrependimento e dentro da garantia) · `100270` · `100281` (loja física) · `100293` · `100301`. As datas são relativas ao dia em que o banco é criado.
