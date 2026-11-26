"""Camada de serviço para validação de filtros e montagem do relatório."""

from dataclasses import dataclass
from typing import cast

from app.repositories.encaminhamentos_repo import (
    EncaminhamentosRepository,
    MetricasEspecialidade,
)
from app.repositories.especialidades_repo import (
    EspecialidadeOption,
    EspecialidadesRepository,
    OrigemEspecialidade,
)


class FiltroEspecialidadeInvalidoError(ValueError):
    """Exceção específica para parâmetros de filtro malformados ou inexistentes."""


@dataclass(frozen=True, slots=True)
class PainelRelatorioDTO:
    """Objeto de transferência pronto para renderização no template Jinja2."""

    especialidades: list[EspecialidadeOption]
    selecionada: EspecialidadeOption | None
    metricas: MetricasEspecialidade


class RelatorioService:
    """Orquestra repositórios e valida parâmetros recebidos da camada HTTP."""

    _ORIGENS_PERMITIDAS: frozenset[str] = frozenset({"sisreg", "dim", "esus_reg"})

    def __init__(
        self,
        especialidades_repo: EspecialidadesRepository,
        encaminhamentos_repo: EncaminhamentosRepository,
    ) -> None:
        self._especialidades_repo = especialidades_repo
        self._encaminhamentos_repo = encaminhamentos_repo

    def carregar_painel(self, filtro_bruto: str | None) -> PainelRelatorioDTO:
        """Carrega o catálogo completo do dropdown e calcula os indicadores solicitados."""
        opcoes = self._especialidades_repo.listar_opcoes_dropdown()
        filtro_limpo = (filtro_bruto or "").strip()

        if not filtro_limpo:
            metricas = self._encaminhamentos_repo.obter_metricas_gerais()
            return PainelRelatorioDTO(
                especialidades=opcoes,
                selecionada=None,
                metricas=metricas,
            )

        origem, codigo = self._parsear_filtro(filtro_limpo)
        indice_opcoes = {item.chave_filtro: item for item in opcoes}
        chave_normalizada = f"{origem}:{codigo}"

        selecionada = indice_opcoes.get(chave_normalizada)
        if selecionada is None:
            raise FiltroEspecialidadeInvalidoError(
                f"A especialidade '{chave_normalizada}' não existe no cadastro atual."
            )

        metricas = self._encaminhamentos_repo.obter_metricas_por_filtro(origem, codigo)
        return PainelRelatorioDTO(
            especialidades=opcoes,
            selecionada=selecionada,
            metricas=metricas,
        )

    def _parsear_filtro(self, valor: str) -> tuple[OrigemEspecialidade, int]:
        partes = valor.split(":", maxsplit=1)
        if len(partes) != 2:
            raise FiltroEspecialidadeInvalidoError(
                "Formato de filtro inválido. Esperado '<origem>:<codigo>'."
            )

        origem_raw, codigo_raw = partes
        if origem_raw not in self._ORIGENS_PERMITIDAS:
            raise FiltroEspecialidadeInvalidoError(
                f"Origem de especialidade desconhecida: '{origem_raw}'."
            )

        try:
            codigo = int(codigo_raw)
        except ValueError as exc:
            raise FiltroEspecialidadeInvalidoError(
                "O código da especialidade deve ser um número inteiro positivo."
            ) from exc

        if codigo <= 0:
            raise FiltroEspecialidadeInvalidoError(
                "O código da especialidade deve ser maior que zero."
            )

        origem = cast("OrigemEspecialidade", origem_raw)
        return origem, codigo
