"""庭审工作流编排：明确的线性 + 并行流水线，避免 Agent 自由对话循环。

START → CasePlanner → Research → SourceRanker → EvidenceAgent
      → (Prosecutor ∥ Defense) → CrossExaminer → Judge → VerdictWriter → END

执行方式：后台线程 + 独立 DB 会话；进度事件写入 case.progress_events（JSON），
SSE 端点按事件序号轮询，天然支持刷新恢复与多 worker。

Fencing：worker 由 manager 通过条件 claim 取得 (generation, lease_token, attempt)。
所有写入（emit / agent_run / 阶段产物 / failure / 最终判决）都在同一事务内先以
id + generation + lease_token + running 条件更新验证执行权，stale 时不写任何数据。
最终提交（Verdict + writer run + engine_mode + verdict_ready + finished_at +
done 事件 + lease 清理 + attempt 终态）原子化在单个事务里。
"""
from __future__ import annotations

import asyncio
import time

from sqlalchemy import select, update

from app.agents import cross_examiner, judge as judge_agent, planner, research, verdict_writer
from app.agents import debate, evidence_agent
from app.core.logging import get_logger
from app.core.url_guard import public_source_url
from app.db.session import SessionLocal
from app.services.llm.gateway import LLMError, llm_case_scope, llm_error_public_message
from app.models import (
    AgentRun,
    Argument,
    Case,
    Claim,
    CrossExamination,
    Evidence,
    Source,
    Verdict,
    gen_id,
    utcnow as gen_utcnow,
)
from app.services import api_usage
from app.services import system_settings
from app.services.cache import cache_set, make_key
from app.services.case_lifecycle import (
    StaleGeneration,
    classify_failure,
    fenced_case,
    mark_attempt_finished,
    mark_run_failed,
)
from app.services.evidence_engine import _authority_score, evidence_relation, independence_via_domain, score_evidence, score_source
from app.services.execution_mode import derive_engine_mode
from app.services.zhihu.search import get_web_search_provider, get_zhihu_provider

logger = get_logger(__name__)

STAGES = [
    "plan", "research", "rank", "evidence", "debate", "cross_exam", "judge", "verdict",
]
STAGE_INDEX = {stage: index for index, stage in enumerate(STAGES)}


class TrialWorkflow:
    def __init__(
        self,
        case_id: str,
        generation: int | None = None,
        lease_token: str | None = None,
        owner: str = "",
        attempt: int = 0,
    ):
        self.case_id = case_id
        self.db = SessionLocal()
        self.generation = generation
        self.lease_token = lease_token
        self.owner = owner
        self.attempt = attempt
        self.current_stage = "init"
        self._event_seq = 0
        self._case_llm_state = None

    # ---------- fencing 基础 ----------
    def _resolve_execution(self) -> bool:
        """未显式传入执行权时从 DB 读取（内部/测试路径）；无租约则拒绝执行。"""
        if self.generation is not None and self.lease_token:
            return True
        case = self.db.get(Case, self.case_id)
        if case is None or not case.lease_token:
            logger.warning("workflow started without lease for case %s; refusing to run", self.case_id)
            return False
        self.generation = case.run_generation or 0
        self.lease_token = case.lease_token
        self.owner = case.lease_owner
        return True

    def _fenced(self) -> Case:
        """开启一个 fenced 事务：验证执行权并续约心跳；stale 时抛出 StaleGeneration。"""
        assert self.generation is not None and self.lease_token
        case = fenced_case(self.db, self.case_id, self.generation, self.lease_token)
        if case is None:
            raise StaleGeneration(f"case {self.case_id} generation {self.generation} lost execution")
        return case

    def _stage(self, stage: str) -> Case:
        self.current_stage = stage
        return self._fenced()

    # ---------- 进度事件 ----------
    def emit(self, stage: str, message: str) -> bool:
        """fenced 事件写入；返回 False 表示已失去执行权（不写、不断言）。"""
        assert self.generation is not None and self.lease_token
        case = fenced_case(self.db, self.case_id, self.generation, self.lease_token)
        if case is None:
            return False
        self.current_stage = stage
        events = list(case.progress_events or [])
        events.append(
            {
                "i": case.progress_index,
                "ts": int(time.time()),
                "stage": stage,
                "message": message,
                "generation": self.generation,
            }
        )
        case.progress_events = events[-300:]
        case.progress_index = (case.progress_index or 0) + 1
        case.current_stage = stage
        self.db.commit()
        return True

    def agent_run(
        self,
        agent: str,
        stage: str,
        mode: str,
        summary: str,
        duration_ms: int,
        error: BaseException | str | None = None,
    ) -> bool:
        """fenced AgentRun 写入；成功运行在同一事务内刷新 Case.engine_mode。"""
        assert self.generation is not None and self.lease_token
        case = fenced_case(self.db, self.case_id, self.generation, self.lease_token)
        if case is None:
            return False
        error_text: str | None = None
        kind = code = ""
        if error is not None:
            exc = error if isinstance(error, BaseException) else RuntimeError(error)
            error_text = str(error)[:300]
            kind, code = classify_failure(exc)
        run = AgentRun(
            id=gen_id("run"), case_id=self.case_id, agent=agent, stage=stage[:40],
            status="failed" if error else "done",
            mode=mode, output_summary=summary[:500], error=error_text, duration_ms=duration_ms,
            run_generation=self.generation, attempt=self.attempt,
            error_kind=kind, error_code=code,
        )
        self.db.add(run)
        if run.status == "done":
            case.engine_mode = derive_engine_mode(list(case.agent_runs) + [run]) or case.engine_mode
        self.db.commit()
        return True

    def _fail(self, stage: str, message: str, kind: str, code: str) -> None:
        """fenced 失败写入；stale 时静默放弃（新持有者会覆盖状态）。"""
        if self.generation is None or not self.lease_token:
            return
        mark_run_failed(
            self.db, self.case_id, self.generation, self.lease_token, self.attempt,
            stage=stage, kind=kind, code=code, message=message,
        )

    def _mark_reused(self, up_to_stage: str) -> None:
        """恢复执行：把被跳过阶段的同代 done 运行标记为 reused（口径仍计入 engine_mode）。"""
        cutoff = STAGE_INDEX.get(up_to_stage, 0)
        skipped = [stage for stage in STAGES if STAGE_INDEX[stage] < cutoff]
        if not skipped:
            return
        assert self.generation is not None
        self.db.execute(
            update(AgentRun)
            .where(
                AgentRun.case_id == self.case_id,
                AgentRun.run_generation == self.generation,
                AgentRun.status == "done",
                AgentRun.stage.in_(skipped),
            )
            .values(status="reused", reused_from_generation=self.generation)
        )

    # ---------- 主流程 ----------
    def run(self) -> None:
        try:
            if self._resolve_execution():
                asyncio.run(self._run_async())
        except StaleGeneration as exc:
            self.db.rollback()
            logger.info("execution fencing stopped stale worker for case %s: %s", self.case_id, exc)
        except LLMError as exc:
            self.db.rollback()
            logger.warning("workflow LLM failure for case %s: %s", self.case_id, exc.code)
            self._fail(
                self.current_stage, llm_error_public_message(exc), "llm_upstream", exc.code,
            )
        except Exception as exc:  # noqa: BLE001
            logger.exception("workflow failed for case %s", self.case_id)
            self.db.rollback()
            kind, code = classify_failure(exc)
            self._fail(
                self.current_stage, f"{type(exc).__name__}: {exc}"[:500], kind, code,
            )
        finally:
            self.db.close()

    async def _run_async(self) -> None:
        # 案件级 LLM 短路 scope：配置级失败（未配置/认证/目标非法）后，本案件
        # 其余 Agent 不再重复调用同一个必然失败的 LLM；瞬时失败不毒化案件。
        # 已配置 LLM 的案件不能在所有主备端点失败后悄悄伪装成启发式成功。
        # gateway 内部已经完成单端点重试与主备切换，此处只处理最终耗尽错误。
        with llm_case_scope(fail_on_error=True) as case_llm_state:
            self._case_llm_state = case_llm_state
            await self._run_async_inner()

    async def _run_async_inner(self) -> None:
        assert self.generation is not None and self.lease_token
        case = self._fenced()
        resume_from = case.resume_from_stage or "plan"
        case.resume_from_stage = ""  # 消费恢复标记，避免下次重复恢复
        if resume_from not in STAGE_INDEX:
            resume_from = "plan"
        self._mark_reused(resume_from)
        self.db.commit()

        if resume_from == "plan":
            self.emit("init", "正在准备开庭……")
        else:
            self.emit("init", f"检测到上次审理中断，正在从「{resume_from}」阶段继续……")
        question = case.original_question
        llm_mode = "llm" if (await self._llm_available()) else "heuristic"

        # 1. Case Planner
        if STAGE_INDEX[resume_from] <= STAGE_INDEX["plan"]:
            t0 = time.time()
            self.emit("plan", "正在理解案件并生成审理命题……")
            try:
                plan = await planner.run_planner(question)
                # planner 可能长时间等待外部 LLM。返回后必须重新 fencing，防止
                # 租约期间已过期并被其他 worker 认领时，旧 worker 覆盖新产物。
                case = self._stage("plan")
                generated_plan = plan.to_dict()
                generated_plan["proposition"] = case.proposition or plan.proposition or question
                generated_plan["title"] = case.title or plan.title
                case.plan = generated_plan
                # 创建页确认后的命题与标题是用户输入，不再被后台二次规划覆盖。
                case.proposition = case.proposition or plan.proposition or question
                case.title = case.title or plan.title
                case.last_completed_stage = "plan"
                self.db.commit()
                self.agent_run("planner", "plan", plan.mode, f"命题：{plan.proposition[:120]}", int((time.time() - t0) * 1000))
            except Exception as exc:  # noqa: BLE001
                self.agent_run("planner", "plan", "heuristic", "", int((time.time() - t0) * 1000), error=exc)
                raise
            if not plan.suitable:
                self.emit("plan", "该问题属于纯事实查询，更适合直接问答，未进入审理。")
                self._fail("plan", "该问题更适合直接问答，不适合进入知识法庭（纯事实查询）。", "validation", "not_suitable")
                return
            self.emit("plan", f"审理命题已确认：{case.proposition}")

            # 1.5 直答 Agent 中立背景（官方直答接口，flag 控制；失败不阻断）
            if system_settings.get_flag(self.db, "enable_direct_answer"):
                self.emit("plan", "直答 Agent 正在构建中立知识背景……")
                try:
                    zhida = get_zhihu_provider(self.db)
                    if getattr(zhida, "is_demo", True) is False and hasattr(zhida, "direct_answer"):
                        da = await zhida.direct_answer(f"请中立、简要地介绍这个问题的背景与主要争议：{case.proposition}")
                        # 直答同样是外部 await；只允许当前 generation/token 持有者落库。
                        case = self._stage("plan")
                        plan_dict = dict(case.plan or {})
                        plan.background = da.answer[:1200]
                        plan_dict["background"] = plan.background
                        case.plan = plan_dict
                        self.db.commit()
                        self.agent_run("direct_answer", "plan", "provider", da.answer[:120], int((time.time() - t0) * 1000))
                except StaleGeneration:
                    raise
                except Exception as exc:  # noqa: BLE001
                    logger.warning("direct answer failed (non-blocking): %s", type(exc).__name__)
                    self.emit("plan", "直答背景暂不可用，将继续基于检索证据审理。")
        else:
            plan = self._plan_from_case(case)

        source_rows: list[Source] = []
        evidence_rows: list[Evidence] = []
        source_by_id: dict[str, Source] = {}
        pro_rows: list[Argument] = []
        def_rows: list[Argument] = []
        cross_rows: list[CrossExamination] = []

        # 2+3. Research → 去重 + Source Ranker（rank 提交持久化来源）
        if STAGE_INDEX[resume_from] <= STAGE_INDEX["rank"]:
            t0 = time.time()
            self.emit("research", "正在构建搜索策略……")
            research_plan = await research.run_research(plan)
            web_enabled = system_settings.get_flag(self.db, "enable_web_search")
            try:
                zhihu_provider = get_zhihu_provider(self.db)
            except Exception:
                zhihu_provider = None
            try:
                web_provider = get_web_search_provider(self.db) if web_enabled else None
            except Exception:
                web_provider = None
            raw_sources: list = []
            zhihu_failed = web_failed = False
            if zhihu_provider is None and web_provider is None:
                self.emit("research", "未配置可用的真实来源且演示来源已关闭，无法检索。")
            elif all(getattr(provider, "is_demo", False) for provider in (zhihu_provider, web_provider) if provider is not None):
                self.emit("research", "正在生成演示检索条目；演示条目并非真实来源。")
            else:
                self.emit("research", "正在检索可用的真实来源；演示来源仅作结构展示。")

            cache_ttl = max(1, min(168, int(system_settings.get_setting(self.db, "search_cache_ttl_hours")))) * 3600

            async def search_zhihu() -> None:
                nonlocal zhihu_failed
                try:
                    for q in research_plan.zhihu_queries[:3]:
                        res = await _cached_search("zhihu_search", q, zhihu_provider, cache_ttl)
                        raw_sources.extend(res)
                except Exception as exc:  # noqa: BLE001
                    zhihu_failed = True
                    logger.warning("zhihu search failed: %s", exc)

            async def search_web() -> None:
                nonlocal web_failed
                try:
                    for q in research_plan.web_queries[:2]:
                        res = await _cached_search("web_search", q, web_provider, cache_ttl)
                        raw_sources.extend(res)
                except Exception as exc:  # noqa: BLE001
                    web_failed = True
                    logger.warning("web search failed: %s", exc)

            tasks = [search_zhihu()] if zhihu_provider is not None else []
            if web_enabled and web_provider is not None:
                tasks.append(search_web())
            elif not web_enabled:
                self.emit("research", "全网搜索已由管理员关闭，本次仅使用可用的知乎来源。")
            else:
                self.emit("research", "全网搜索 Provider 不可用，本次仅使用其他可用来源。")
            await asyncio.gather(*tasks)
            self.agent_run("research", "research", research_plan.mode, f"知乎查询 {len(research_plan.zhihu_queries)} / 全网查询 {len(research_plan.web_queries)}", int((time.time() - t0) * 1000))
            if not raw_sources:
                raise RuntimeError("无可用搜索来源，未获得可追溯证据，无法构建案件")
            if zhihu_failed:
                self.emit("research", "知乎来源暂时不可用，将继续使用可用来源完成审理。")
            if web_failed:
                self.emit("research", "全网搜索暂时不可用，将继续使用知乎内容完成审理。")

            # 3. 去重 + Source Ranker
            self.emit("rank", f"初步获得 {len(raw_sources)} 条候选内容，正在去重与筛选高质量来源……")
            deduped = _dedupe(raw_sources)
            source_rows = self._build_sources(case, deduped)
            independence_via_domain(source_rows)
            for s in source_rows:
                s.authority_score = _authority_score(s)
                s.rank_score = score_source(s)
            source_rows.sort(key=lambda s: s.rank_score, reverse=True)
            source_limit = max(1, min(50, int(system_settings.get_setting(self.db, "max_sources_per_case"))))
            source_rows = source_rows[:source_limit]
            case = self._stage("rank")
            self.db.add_all(source_rows)
            case.last_completed_stage = "rank"
            self.db.commit()
            self.emit("rank", f"已筛选出 {len(source_rows)} 条高质量来源。")
        else:
            source_rows = list(
                self.db.execute(
                    select(Source).where(Source.case_id == self.case_id).order_by(Source.rank_score.desc(), Source.id)
                ).scalars().all()
            )

        # 4. Evidence Agent
        if STAGE_INDEX[resume_from] <= STAGE_INDEX["evidence"]:
            t0 = time.time()
            self.emit("evidence", "Evidence Agent 正在从来源中提取结构化证据……")
            analysis_mode = "heuristic" if case.is_demo else llm_mode
            ev_items, ev_mode = await evidence_agent.run_evidence_agent(case.proposition, source_rows, mode=analysis_mode)
            if ev_mode == "llm":
                self.emit("evidence", "全部来源已完成 LLM 分批精读与结构化提取。")
            elif ev_mode == "mixed":
                self.emit("evidence", "部分来源完成 LLM 精读；超时批次已安全降级为真实来源摘要提取。")
            else:
                self.emit("evidence", "未配置 LLM，按来源摘要规则提取；演示来源不构成现实证据。" if analysis_mode != "llm" else "LLM 精读未成功，已按来源摘要规则降级提取；演示来源不构成现实证据。")
            evidence_rows = evidence_agent.build_evidence_rows(case.id, source_rows, ev_items)
            top_k = int(system_settings.get_setting(self.db, "evidence_top_k_per_source"))
            per_source: dict[str, int] = {}
            selected = []
            for ev in evidence_rows:
                if per_source.get(ev.source_id, 0) < top_k:
                    selected.append(ev)
                    per_source[ev.source_id] = per_source.get(ev.source_id, 0) + 1
            evidence_rows = selected
            source_by_id = {s.id: s for s in source_rows}
            for ev in evidence_rows:
                ev.source = source_by_id[ev.source_id]
            for ev in evidence_rows:
                source = source_by_id[ev.source_id]
                corr = len({o.source_id for o in evidence_rows if evidence_relation(ev, o) and not source_by_id[o.source_id].is_demo})
                ev.relevance_score = source.relevance_score
                ev.authority_score = source.authority_score
                ev.strength = score_evidence(ev, source, corroborated_by=corr)
            case = self._stage("evidence")
            self.db.add_all(evidence_rows)
            case.last_completed_stage = "evidence"
            self.db.commit()
            self.agent_run("evidence", "evidence", ev_mode, f"提取 {len(evidence_rows)} 条结构化条目（演示条目不构成现实证据）", int((time.time() - t0) * 1000))
            self.emit("evidence", f"已得到 {len(evidence_rows)} 条核心证据。")
            if not evidence_rows:
                self.emit("evidence", "未能提取到有效证据，本次审理终止。")
                self._fail("evidence", "未能从来源中提取到有效证据，请稍后重试或更换问题。", "validation", "no_evidence")
                return
        else:
            evidence_rows = list(
                self.db.execute(
                    select(Evidence).where(Evidence.case_id == self.case_id).order_by(Evidence.created_at, Evidence.id)
                ).scalars().all()
            )
            source_by_id = {s.id: s for s in source_rows}
            for ev in evidence_rows:
                ev.source = source_by_id[ev.source_id]
            analysis_mode = "heuristic" if case.is_demo else llm_mode

        debate_evidence = [e for e in evidence_rows if not source_by_id[e.source_id].is_demo]

        # 5. Prosecutor ∥ Defense（并行）
        if STAGE_INDEX[resume_from] <= STAGE_INDEX["debate"]:
            t0 = time.time()
            self.emit("debate", "原告 Agent 正在组织支持论证……")
            self.emit("debate", "被告 Agent 正在寻找反例与反对论证……")
            # 控辩真正并行：两边无数据依赖，可显著缩短真实 LLM 模式耗时。
            pro_result, def_result = await asyncio.gather(
                debate.run_advocate("prosecution", case.proposition, debate_evidence, mode=analysis_mode),
                debate.run_advocate("defense", case.proposition, debate_evidence, mode=analysis_mode),
            )
            pro_claims, pro_args, pro_mode = pro_result
            def_claims, def_args, def_mode = def_result
            pro_claim_rows = debate.build_claim_rows(case.id, pro_claims, "pro")
            def_claim_rows = debate.build_claim_rows(case.id, def_claims, "con")
            claim_rows = pro_claim_rows + def_claim_rows
            arg_rows = debate.build_argument_rows(
                case.id, pro_claims, pro_args, "prosecution", claim_rows=pro_claim_rows
            ) + debate.build_argument_rows(
                case.id, def_claims, def_args, "defense", claim_rows=def_claim_rows
            )
            case = self._stage("debate")
            self.db.add_all([*arg_rows, *claim_rows])
            case.last_completed_stage = "debate"
            self.db.commit()
            self.agent_run("prosecutor", "debate", pro_mode, f"{len(pro_args)} 个论证", int((time.time() - t0) * 1000))
            self.agent_run("defense", "debate", def_mode, f"{len(def_args)} 个论证", int((time.time() - t0) * 1000))
            self.emit("debate", "双方论证已提交。")
            pro_rows = [a for a in arg_rows if a.side == "prosecution"]
            def_rows = [a for a in arg_rows if a.side == "defense"]
        else:
            arg_rows = list(
                self.db.execute(
                    select(Argument).where(Argument.case_id == self.case_id).order_by(Argument.created_at, Argument.id)
                ).scalars().all()
            )
            pro_rows = [a for a in arg_rows if a.side == "prosecution"]
            def_rows = [a for a in arg_rows if a.side == "defense"]

        # 6. Cross Examiner
        if STAGE_INDEX[resume_from] <= STAGE_INDEX["cross_exam"]:
            t0 = time.time()
            self.emit("cross_exam", "Cross Examiner 正在检查逻辑漏洞与证据质量问题……")
            cross_items, cross_mode = await cross_examiner.run_cross_examiner(
                case.proposition, pro_rows, def_rows, debate_evidence, source_rows, mode=analysis_mode
            )
            cross_rows = [
                CrossExamination(
                    id=gen_id("cx"), case_id=case.id,
                    target_side=str(x.get("target_side", "both")),
                    issue_type=str(x.get("issue_type", "other")),
                    severity=str(x.get("severity", "medium")),
                    description=str(x.get("description", "")),
                    related_evidence_ids=list(x.get("related_evidence_ids", []))[:6],
                )
                for x in cross_items
            ]
            case = self._stage("cross_exam")
            self.db.add_all(cross_rows)
            case.last_completed_stage = "cross_exam"
            self.db.commit()
            self.agent_run("cross_examiner", "cross_exam", cross_mode, f"发现 {len(cross_rows)} 个问题", int((time.time() - t0) * 1000))
            self.emit("cross_exam", f"质证完成，发现 {len(cross_rows)} 个需要标注的问题。")
        else:
            cross_rows = list(
                self.db.execute(
                    select(CrossExamination).where(CrossExamination.case_id == self.case_id).order_by(CrossExamination.created_at, CrossExamination.id)
                ).scalars().all()
            )

        # 7. Judge（judge 结果不单独持久化，恢复时必然重跑）
        t0 = time.time()
        self.emit("judge", "Judge Agent 正在基于证据结构进行审理……")
        judge_result, judge_mode = await judge_agent.run_judge(
            case.proposition, pro_rows, def_rows, debate_evidence, cross_rows,
            plan.disputes, plan.sub_questions, mode=analysis_mode,
        )
        self.agent_run("judge", "judge", judge_mode, f"结论：{judge_result['conclusion_stance']} 置信度 {judge_result['confidence']}", int((time.time() - t0) * 1000))

        # 8. Verdict Writer —— 最终提交原子化：判决、writer run、engine_mode、
        #    verdict_ready、finished_at、done 事件与 lease 清理在同一个 fenced 事务。
        t0 = time.time()
        if not self.emit("verdict", "正在生成《知识判决书》……"):
            return
        pro_summary = "；".join(a.title for a in pro_rows[:3]) or "（见庭审页控方论证）"
        defense_summary = "；".join(a.title for a in def_rows[:3]) or "（见庭审页辩方论证）"
        issue_names = {
            "concept_swap": "偷换概念", "causal_inversion": "因果倒置", "correlation_not_causation": "相关不等于因果",
            "overgeneralization": "过度泛化", "sample_bias": "样本偏差", "survivorship_bias": "幸存者偏差",
            "insufficient_evidence": "证据不足", "recency_risk": "证据时效性风险", "scope_mismatch": "适用范围错误",
            "definition_conflict": "定义冲突", "evidence_argument_mismatch": "论点与证据不匹配",
            "missing_counterexample": "缺乏反例", "source_concentration": "证据来源过于集中",
            "data_quality": "数据质量问题", "opinion_as_fact": "观点被当作事实", "prediction_as_fact": "预测被当作事实",
        }
        cross_summary = (
            f"质证发现 {len(cross_rows)} 个问题："
            + "；".join(issue_names.get(c.issue_type, c.issue_type) for c in cross_rows[:5])
        )
        writer_result, writer_mode = await verdict_writer.run_verdict_writer(
            judge_result, pro_summary, defense_summary, cross_summary, mode=analysis_mode
        )
        case = self._stage("verdict")
        verdict = Verdict(
            id=gen_id("vd"), case_id=case.id,
            conclusion=judge_result["conclusion"],
            conclusion_stance=judge_result["conclusion_stance"],
            confidence=judge_result["confidence"],
            prosecution_summary=writer_result["prosecution_summary"],
            defense_summary=writer_result["defense_summary"],
            shared_facts=judge_result["shared_facts"],
            core_disputes=judge_result["core_disputes"],
            strongest_evidence_ids=judge_result["strongest_evidence_ids"],
            strongest_counter_evidence_ids=judge_result["strongest_counter_evidence_ids"],
            evidence_gaps=judge_result["evidence_gaps"],
            definition_conflicts=judge_result["definition_conflicts"],
            unknowns=judge_result["unknowns"],
            verdict_changers=judge_result["verdict_changers"],
            next_questions=judge_result["next_questions"],
            cross_exam_summary=writer_result["cross_exam_summary"],
        )
        self.db.add(verdict)
        writer_run = AgentRun(
            id=gen_id("run"), case_id=self.case_id, agent="verdict_writer", stage="verdict",
            status="done", mode=writer_mode, output_summary="判决书已生成", duration_ms=int((time.time() - t0) * 1000),
            run_generation=self.generation, attempt=self.attempt,
        )
        self.db.add(writer_run)
        case.engine_mode = derive_engine_mode(list(case.agent_runs) + [writer_run]) or case.engine_mode
        case.status = "verdict_ready"
        case.current_stage = "done"
        case.last_completed_stage = "verdict"
        case.finished_at = gen_utcnow()
        case.resume_from_stage = ""
        case.failure_stage = ""
        case.failure_kind = ""
        case.failure_code = ""
        events = list(case.progress_events or [])
        events.append(
            {
                "i": case.progress_index,
                "ts": int(time.time()),
                "stage": "done",
                "message": "案件审理完成，判决书已生成。",
                "generation": self.generation,
            }
        )
        case.progress_events = events[-300:]
        case.progress_index = (case.progress_index or 0) + 1
        # 最终提交：释放 lease（ fencing 终态）
        case.lease_token = ""
        case.lease_owner = ""
        case.lease_expires_at = None
        case.lease_heartbeat_at = None
        mark_attempt_finished(self.db, self.case_id, self.attempt, last_stage="verdict")
        self.db.commit()

    def _plan_from_case(self, case: Case) -> planner.TrialPlan:
        """恢复执行时从 case.plan JSON 重建规划对象（plan 是已提交的持久化产物）。"""
        data = dict(case.plan or {})
        return planner.TrialPlan(
            title=str(data.get("title", "")),
            proposition=str(data.get("proposition", "")) or case.proposition,
            key_concepts=list(data.get("key_concepts") or []),
            disputes=list(data.get("disputes") or []),
            sub_questions=list(data.get("sub_questions") or []),
            search_queries=list(data.get("search_queries") or []),
            suitable=bool(data.get("suitable", True)),
            needs_rewrite=bool(data.get("needs_rewrite", False)),
            mode=str(data.get("mode", "heuristic")),
            background=str(data.get("background", "")),
            input_classification=str(data.get("input_classification", "debatable")),
            rejection_reason=data.get("rejection_reason"),
        )

    async def _llm_available(self) -> bool:
        from app.services import provider_config

        # Provider settings are persisted in DB while every Gunicorn worker has
        # its own gateway instance. Synchronize at the case boundary so newly
        # saved primary/fallback endpoints work without a process restart.
        return provider_config.sync_llm_gateway(self.db).enabled

    def _build_sources(self, case: Case, results: list) -> list[Source]:
        rows: list[Source] = []
        seen_titles: set[str] = set()
        for r in results:
            key = r.title.strip().lower()[:60]
            if key in seen_titles:
                continue
            seen_titles.add(key)
            source = Source(
                    id=gen_id("src"), case_id=case.id,
                    origin=r.origin, kind=r.kind,
                    title=r.title[:300], url=public_source_url(r.url)[:600],
                    author=r.author[:100], summary=r.summary[:2000],
                    published_at=r.published_at[:30],
                    vote_count=r.vote_count, comment_count=r.comment_count,
                    relevance_score=0.7, is_demo=getattr(r, "is_demo_source", False),
                )
            source.authority_level = getattr(r, "official_authority_level", None)
            rows.append(source)
        case.is_demo = bool(rows) and all(s.is_demo for s in rows)
        return rows


# ---------- 搜索缓存包装 ----------
async def _cached_search(provider_name: str, query: str, provider, ttl_seconds: int = 6 * 3600) -> list:
    """带缓存与限额保护的搜索。缓存命中不消耗官方额度。"""
    from app.services.cache import cache_get

    provider_identity = "demo" if provider.is_demo else make_key(
        provider.base_url, provider.api_key, provider.paths.get(f"{provider_name}_path", "")
    )
    key = f"search:{provider_name}:{provider_identity}:{make_key(query)}"
    hit = cache_get(key)
    if hit is not None:
        api_usage.record(provider_name, "search", cache_hit=True)
        from app.services.zhihu.schemas import NormalizedSearchResult

        return [NormalizedSearchResult(**item) for item in hit]

    if api_usage.near_limit(provider_name):
        logger.warning("provider %s near daily limit, skipping live search for: %s", provider_name, query[:40])
        return []

    results = await provider.search(query)
    if results:
        cache_set(key, [r.model_dump() for r in results], ttl_seconds=ttl_seconds)
    return results


def _dedupe(results: list) -> list:
    """按 URL 与标题去重。"""
    seen_urls: set[str] = set()
    seen_titles: set[str] = set()
    out: list = []
    for r in results:
        k1 = r.url.strip().lower()
        k2 = r.title.strip().lower()[:80]
        if (k1 and k1 in seen_urls) or (k2 and k2 in seen_titles):
            continue
        if k1:
            seen_urls.add(k1)
        if k2:
            seen_titles.add(k2)
        out.append(r)
    return out
