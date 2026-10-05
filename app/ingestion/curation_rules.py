"""
Regras determinísticas de curadoria do corpus RAG (Etapa 1).

Princípios:
- Toda decisão é explicável: cada regra tem um `rule_id` estável, registrado no resultado.
- Precedência explícita: a PRIMEIRA regra de título que casar vence. A ordem codifica o
  conhecimento de domínio (ex.: "Modelo - Contrato..." é modelo antes de ser contrato;
  "Análise do Contrato..." é parecer; "Petição ... Honorários" é peça processual).
- Título > pasta de origem > classe M-Files (do sinal mais específico ao mais genérico).
- Qualquer alteração de regra exige incrementar RULES_VERSION (invalida o cache de decisões
  e torna a mudança auditável).
"""

import re
import unicodedata
from dataclasses import dataclass
from enum import StrEnum
from typing import Optional

RULES_VERSION = "2026.10.05-3"


class Decision(StrEnum):
    INCLUDE = "include"
    EXCLUDE = "exclude"
    REVIEW = "review"


class Category(StrEnum):
    INSTRUMENTO_CONTRATUAL = "instrumento_contratual"
    ADITIVO = "aditivo"
    DISTRATO = "distrato"
    ACORDO = "acordo"
    PROPOSTA_HONORARIOS = "proposta_honorarios"
    ATO_SOCIETARIO = "ato_societario"
    CONTRATO_PADRAO = "contrato_padrao"
    MODELO = "modelo"
    MINUTA = "minuta"
    PECA_PROCESSUAL = "peca_processual"
    PARECER_ANALISE = "parecer_analise"
    DOCUMENTO_PESSOAL = "documento_pessoal"
    DOCUMENTO_ADMINISTRATIVO = "documento_administrativo"
    NAO_TEXTUAL = "nao_textual"
    INDETERMINADO = "indeterminado"


# Decisão de escopo (CONTEXT.md §3.1.5): categorias que entram no índice RAG.
INCLUDED_CATEGORIES = frozenset({
    Category.INSTRUMENTO_CONTRATUAL,
    Category.ADITIVO,
    Category.DISTRATO,
    Category.ACORDO,
    Category.PROPOSTA_HONORARIOS,
    Category.ATO_SOCIETARIO,
    Category.CONTRATO_PADRAO,
})


def decision_for(category: Category) -> Decision:
    if category is Category.INDETERMINADO:
        return Decision.REVIEW
    return Decision.INCLUDE if category in INCLUDED_CATEGORIES else Decision.EXCLUDE


def normalize(text: str) -> str:
    """Minúsculas, sem acentos, sem extensão de arquivo no fim, separadores viram espaço."""
    text = unicodedata.normalize("NFKD", text or "")
    text = "".join(ch for ch in text if not unicodedata.combining(ch)).casefold()
    text = re.sub(r"\.(docx?|rtf|pdf|odt)\s*$", "", text.strip())
    text = re.sub(r"[_\-–—|/\\.,;:()\[\]\"']+", " ", text)
    return re.sub(r"\s+", " ", text).strip()


@dataclass(frozen=True)
class Rule:
    rule_id: str
    category: Category
    pattern: re.Pattern
    description: str


def _r(rule_id: str, category: Category, pattern: str, description: str) -> Rule:
    return Rule(rule_id, category, re.compile(pattern), description)


# Termos (já normalizados) que caracterizam peças processuais.
_PECA = (
    r"peticao|peticoes|contestacao|contrarrazoes|razoes|recurso|apelacao|agravo|embargos|replica|"
    r"memoriais|memorial|alegacoes finais|impugnacao|excecao de pre executividade|mandado de seguranca|"
    r"manifestacao|manifestar|contraminuta|quesitos|juntada|habilitacao|execucao|cumprimento de sentenca|"
    r"emenda a inicial|inicial|defesa|reconsideracao|homologacao|discriminacao de verbas|"
    r"abertura de testamento|inventario|arrolamento|representacao disciplinar|ciencia de oficios|"
    r"oficio|requerimento|convite de testemunha|reserva honorarios|arbitramento|acao|pedido|req|"
    r"esclarecimentos|retirada de pauta"
)

# Título que COMEÇA com um substantivo de instrumento é contratual mesmo que o objeto cite termos
# processuais (ex.: "Contrato de Honorários - Ação Judicial Avulsa - Inventário").
_ORDINAL = r"(?:(?:primeir|segund|terceir|quart|quint|sext|setim|oitav|non|decim)[oa] |\d+ ?[oa]? )?"
CONTRACT_HEAD = re.compile(
    rf"^{_ORDINAL}(contrato|contratos|instrumento|intrumento|termo (de|aditivo|complementar)|aditivo|"
    r"distrato|proposta|acordo|confissao de divida|compromisso de|promessa de|escritura|memorando de entendimento|"
    r"mou|alteracao|anexo [ivx]+ (instrumento|intrumento|contrato|termo))\b"
)

TITLE_RULES: tuple[Rule, ...] = (
    # 1. Modelos e minutas (excluídos por decisão de escopo) — precedem qualquer tipo contratual.
    _r("T01_MODELO", Category.MODELO, r"\bmodelos?\b|\btemplate\b", "Título declara modelo/template genérico."),
    _r("T02_MINUTA", Category.MINUTA,
       r"\bminutas?\b|\bdraft\w*|\brev \d+\b|\bversao (enviada|revisada|com correc)|\brevisad[ao] pel[ao]\b|"
       r"\brevisao d[ao]\b|\bsugestoes\b|\bcom correc(ao|oes)\b",
       "Título declara minuta, rascunho ou versão em negociação."),
    # 2. Análises, pareceres, estudos e relatórios (citam contratos, mas não são instrumentos).
    _r("T03_PARECER", Category.PARECER_ANALISE,
       r"\b(analise|parecer|opiniao legal|estudo|observacoes|consideracoes|vantagens e desvantagens|"
       r"due diligence|report|relatorio|resumo)\b",
       "Título indica análise, parecer, estudo ou relatório."),
    # Instrumentos normativos coletivos (CCT/ACT) e seus aditivos: fora do escopo, de forma coerente
    # (precede o "substantivo inicial", senão "Termo aditivo da Convenção Coletiva" viraria aditivo órfão).
    _r("T03B_NORMATIVO", Category.DOCUMENTO_ADMINISTRATIVO,
       r"\b(convencao coletiva|acordo coletivo|dissidio coletivo)\b",
       "Instrumento normativo coletivo (CCT/ACT) ou aditivo dele — fora do escopo contratual."),
    # Ambíguos por natureza: sempre revisão humana (categoria indeterminada).
    _r("T03C_AMBIGUO", Category.INDETERMINADO,
       r"\b(homologacao de acordo|nota promissoria|proposta(?! (ajur|de honorarios|comercial de honorarios)))\b",
       "Título ambíguo (petição de homologação, título de crédito, proposta não-honorária): revisão."),
    # 3. Peças processuais.
    _r("T04_PECA", Category.PECA_PROCESSUAL, rf"\b({_PECA})\b", "Título indica peça ou ato processual."),
    # 4. Documentos pessoais.
    _r("T05_PESSOAL", Category.DOCUMENTO_PESSOAL,
       r"\b(documento pessoal|documentos pessoais|rg|cnh|cpf|certidao|comprovante|ctps|"
       r"certidao de obito|identidade|matricula)\b",
       "Título indica documento pessoal ou certidão."),
    # 5. Documentos administrativos, de representação e comunicações (não contratuais).
    _r("T06_ADMIN", Category.DOCUMENTO_ADMINISTRATIVO,
       r"\b(procuracao|substabelecimento|subst|revogacao de poderes|renuncia de mandato|carta de preposicao|"
       r"preposicao|declaracao|timbrado|capa|planilha|calculo|fatura|boleto|nota fiscal|pcmso|ppra|"
       r"ltcat|pgr|notificacao|notificacoes|comunicado|carta(?! de intencao)|protocolo|ficha|normas|"
       r"regulamento|regras|politica|planejamento|representacao processual|demissao|aviso previo)\b",
       "Título indica documento administrativo, normativo, de representação ou comunicação."),
    # 6. Tipos contratuais (do mais específico ao mais genérico).
    _r("T07_SOCIETARIO", Category.ATO_SOCIETARIO,
       r"\b(contrato social|alteracao contratual|alteracao do contrato social|acordo de (quotistas|cotistas|socios)|"
       r"distrato social|constituicao de (sociedade|holding|consorcio)|cessao de (quotas|cotas)|"
       r"(compra|venda) (e (venda|compra) )?de (quotas|cotas)|doacao (de )?(quotas|cotas)|"
       r"renuncia de preferencia|re ratificacao|ata de (assembleia|reuniao)|\d+ ?[ao]? alteracao|(primeira|segunda|terceira|quarta|quinta|"
       r"sexta|setima|oitava|nona|decima) alteracao|termo de consorcio|termo consorcio)\b",
       "Título indica ato societário (contrato social, alteração, acordo de quotistas, consórcio)."),
    _r("T08_PADRAO", Category.CONTRATO_PADRAO, r"\bpadrao\b", "Contrato-padrão do cliente (incluído por decisão de escopo)."),
    _r("T09_ADITIVO", Category.ADITIVO, r"\b(aditivo|aditamento|termo complementar)\b", "Título indica aditivo/termo complementar."),
    _r("T10_DISTRATO", Category.DISTRATO,
       r"\b(distrato|rescisao|termo de quitacao|encerramento (de )?prestacao)\b",
       "Título indica distrato, rescisão ou quitação contratual."),
    _r("T11_PROPOSTA", Category.PROPOSTA_HONORARIOS,
       r"\b(proposta|proposal)\b", "Título indica proposta comercial/de honorários."),
    _r("T12_ACORDO", Category.ACORDO,
       r"\b(acordo|transacao|memorando de entendimentos?|mou|carta de intencao|loi|"
       r"termo de ajustamento de conduta|tac)\b",
       "Título indica acordo, transação, TAC, carta de intenção ou memorando de entendimentos."),
    _r("T13_INSTRUMENTO", Category.INSTRUMENTO_CONTRATUAL,
       r"\b(contrato|contratos|instrumento|intrumento|confissao de divida|cessao|compromisso de (venda|compra)|"
       r"promessa de (venda|compra|cessao)|comodato|mutuo|locacao|sublocacao|arrendamento|escritura|"
       r"termo de (adesao|autorizacao|uso|ciencia|responsabilidade|ressarcimento|comodato|confidencialidade|"
       r"renovacao|responsabilizacao|compromisso|entrega|recebimento)|nda|confidencialidade|licenca de uso|"
       r"assuncao)\b",
       "Título indica instrumento contratual."),
)

# Pastas de origem (sistema de arquivos legado do escritório, propriedade "Caminho original").
FOLDER_RULES: tuple[Rule, ...] = (
    _r("F01_PECA", Category.PECA_PROCESSUAL,
       r"\bcontencioso (judicial|administrativo)\b(?! (acordos|docs representacao))",
       "Pasta processual do Contencioso (exceto Acordos e Docs Representação/Atos Constitutivos)."),
    _r("F02_PARECER", Category.PARECER_ANALISE, r"\bopinioes legais\b", "Pasta de Opiniões Legais."),
    _r("F03_SOCIETARIO", Category.ATO_SOCIETARIO,
       r"\bconsultivo contratos contrato de constituicao de\b", "Pasta de constituição de sociedades/consórcios."),
    _r("F04_CONTRATO", Category.INSTRUMENTO_CONTRATUAL, r"\bconsultivo contratos\b", "Pasta Consultivo\\Contratos."),
    _r("F05_ACORDO", Category.ACORDO, r"\bcontencioso judicial acordos\b", "Pasta Contencioso\\Acordos."),
)
# Regras de pasta que decidem sozinhas (exclusões estruturais). As demais são "fracas":
# só decidem se o classificador (Jev) concordar; caso contrário o documento vai para revisão.
STRONG_FOLDER_RULES = frozenset({"F01_PECA", "F02_PARECER"})

_NON_CONTRACT_TITLE_IDS = ("T04_PECA", "T05_PESSOAL", "T06_ADMIN")

CONTRACT_CLASSES = frozenset({"contrato", "acordo", "acordo extrajudicial", "proposta / orcamento", "proposta orcamento"})

# Sinais fracos em Palavras-chave: não decidem sozinhos, apenas forçam revisão.
KEYWORD_FLAGS = (
    ("K01_KW_MODELO", re.compile(r"\bmodelos?\b")),
    ("K02_KW_MINUTA", re.compile(r"\bminutas?\b")),
)


@dataclass(frozen=True)
class RuleMatch:
    category: Category
    rule_id: str
    source: str                      # "title" | "folder"
    description: str
    strong: bool = True              # False: precisa da concordância do classificador
    overridden: Optional[str] = None  # regra de título vencida pela pasta processual (auditoria)


def original_folder(properties: dict) -> str:
    """Reconstrói a pasta de origem (propriedade 'Caminho original' fatiada em 3) a partir de 'AACloud'."""
    raw = "".join(properties.get(f"Caminho original ({i}/3)", "") for i in (1, 2, 3)).rstrip("|")
    if not raw:
        return ""
    raw = raw.replace("/", "\\")
    if "AACloud\\" in raw:
        raw = raw.split("AACloud\\", 1)[1]
    else:
        raw = re.sub(r"^[A-Za-z]:\\(Users\\[^\\]+\\)?", "", raw)
    parts = raw.split("\\")
    return "\\".join(parts[:-1]) if len(parts) > 1 else ""


def classify_title(title: str) -> Optional[RuleMatch]:
    """
    1. Modelo/minuta/parecer sempre vencem (são exclusões de escopo).
    2. Se o título começa com substantivo de instrumento, pula as regras de peça/admin/pessoal.
    3. Caso contrário, regras na ordem declarada.
    """
    norm = normalize(title)
    skip = _NON_CONTRACT_TITLE_IDS if CONTRACT_HEAD.search(norm) else ()
    for rule in TITLE_RULES:
        if rule.rule_id in skip:
            continue
        if rule.pattern.search(norm):
            return RuleMatch(rule.category, rule.rule_id, "title", rule.description)
    return None


def classify_folder(folder: str) -> Optional[RuleMatch]:
    norm = normalize(folder)
    for rule in FOLDER_RULES:
        if rule.pattern.search(norm):
            return RuleMatch(rule.category, rule.rule_id, "folder", rule.description,
                             strong=rule.rule_id in STRONG_FOLDER_RULES)
    return None


def classify(title: str, folder: str) -> Optional[RuleMatch]:
    """
    Título > pasta, com uma exceção: pasta processual do Contencioso (F01) vence um título
    contratual (ex.: "Acordo cumprido - X vs Y" em Manifestações intermediárias é peça).
    A classe M-Files sozinha NÃO decide (há petições e regulamentos nas classes contratuais).
    """
    by_title = classify_title(title)
    by_folder = classify_folder(folder)
    if by_title and by_folder and by_folder.rule_id == "F01_PECA" and by_title.category in INCLUDED_CATEGORIES:
        return RuleMatch(by_folder.category, by_folder.rule_id, "folder", by_folder.description,
                         strong=True, overridden=by_title.rule_id)
    return by_title or by_folder


def keyword_flags(keywords: str) -> list[str]:
    norm = normalize(keywords)
    return [flag for flag, pattern in KEYWORD_FLAGS if pattern.search(norm)]


def is_contract_class(class_name: str) -> bool:
    return normalize(class_name) in {normalize(c) for c in CONTRACT_CLASSES}


# --- Famílias de versões -------------------------------------------------------------------
# Um título "(2)" vira o marcador sintético "versao" antes da normalização.
_VERSION_MARKER = re.compile(
    r"\b(versao|revisad[ao]|revisao|rev|draft\w*|final|finalizad[ao]|retificad[ao]|ver ?\d+)\b"
)
_FINAL_MARKER = re.compile(r"\b(versao final|finalizad[ao]|final|assinad[ao])\b")
_STOPWORDS = re.compile(r"\b(a|o|as|os|de|da|do|das|dos|e|com|pela|pelo|para|em|no|na|ao)\b")


def _marked(title: str) -> str:
    return normalize(re.sub(r"\(\d+\)", " versao ", title or ""))


def _stem(text: str) -> str:
    return re.sub(r"\s+", " ", _STOPWORDS.sub(" ", text)).strip()


def has_version_marker(title: str) -> bool:
    return bool(_VERSION_MARKER.search(_marked(title)))


def family_stem(title: str) -> str:
    """
    Chave de família: prefixo do título antes do primeiro marcador de versão (sem stopwords).
    Ex.: "Contrato de Comodato - ArtBrasil - Versão com correções" e "... - Versão finalizada"
    -> "contrato comodato artbrasil". Títulos sem marcador usam o título inteiro.
    """
    marked = _marked(title)
    match = _VERSION_MARKER.search(marked)
    return _stem(marked[: match.start()] if match else marked)


def looks_final(title: str) -> bool:
    return bool(_FINAL_MARKER.search(_marked(title)))


__all__ = [
    "RULES_VERSION", "Decision", "Category", "INCLUDED_CATEGORIES", "decision_for", "normalize",
    "RuleMatch", "classify", "classify_title", "classify_folder", "keyword_flags", "original_folder",
    "is_contract_class", "has_version_marker", "family_stem", "looks_final", "TITLE_RULES", "FOLDER_RULES",
]
