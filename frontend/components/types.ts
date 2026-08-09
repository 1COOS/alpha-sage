export type View =
  | "today"
  | "research"
  | "portfolio"
  | "memory"
  | "evolution"
  | "chat"
  | "settings";

export type SourceHealth = {
  source_id: string;
  role: string;
  status: string;
  detail?: string;
  last_checked_at?: string;
};

export type SystemStatus = {
  account_enabled: boolean;
  account_cash: string;
  equity: string;
  drawdown: string;
  current_strategy: string;
  last_run: Record<string, unknown> | null;
  source_health: SourceHealth[];
  blockers: string[];
};

export type AgentRun = {
  id: string;
  kind: string;
  status: "PENDING" | "RUNNING" | "COMPLETED" | "BLOCKED" | "FAILED" | "SKIPPED";
  trigger_source: string;
  parameters: Record<string, unknown>;
  trade_date?: string | null;
  stage?: string | null;
  progress_current?: number | null;
  progress_total?: number | null;
  progress_message?: string | null;
  blocker?: string | null;
  result: Record<string, unknown>;
  started_at: string;
  updated_at?: string | null;
  finished_at?: string | null;
};

export type RunAccepted = {
  run_id: string;
  kind: string;
  status: AgentRun["status"];
  stage?: string | null;
  message?: string | null;
};

export type LocalActionFeedback = {
  id: string;
  label: string;
  status: "RUNNING" | "COMPLETED" | "FAILED";
  message: string;
  detail?: string;
  started_at: string;
  finished_at?: string;
};

export type ActionFeedbackSummary = {
  id: string;
  label: string;
  status: AgentRun["status"] | LocalActionFeedback["status"];
  message: string;
  stage?: string | null;
};

export type ActionFeedbackMap = ReadonlyMap<string, ActionFeedbackSummary>;

export type Research = {
  id: string;
  instrument_id: string;
  symbol: string;
  name: string;
  trade_date: string;
  thesis: {
    summary?: string;
    catalysts?: string[];
    supporting_claims?: string[];
  };
  opposition: {
    strongest_counter_thesis?: string;
    failure_modes?: string[];
    evidence_gaps?: string[];
  };
  synthesis: {
    verdict?: string;
    summary?: string;
    horizon_views?: Array<{
      horizon: string;
      action: string;
      confidence: number;
      target_weight: number;
      rationale: string;
    }>;
  };
  evidence_ids: string[];
  created_at: string;
};

export type Order = {
  id: string;
  instrument_id: string;
  symbol: string;
  name: string;
  horizon: string;
  side: string;
  quantity: number;
  filled_quantity: number;
  status: string;
  blocked_reason?: string;
  created_at: string;
};

export type Fill = {
  id: string;
  symbol: string;
  name: string;
  horizon: string;
  side: string;
  quantity: number;
  fill_price: string;
  commission: string;
  tax: string;
  currency: string;
  local_trade_date: string;
  filled_at: string;
};

export type Portfolio = {
  account: null | {
    enabled: boolean;
    paused_reason?: string;
    cash: string;
  };
  positions: Array<{
    instrument_id: string;
    symbol: string;
    name: string;
    horizon: string;
    quantity: number;
    cost: string;
    price: string;
    market_value: string;
    unrealized_pnl: string;
  }>;
  cash: string;
  market_value: string;
  equity: string;
  drawdown: string;
  risk_state: string;
  horizon_values: Record<string, string>;
};

export type Experience = {
  id: string;
  horizon: string;
  thesis_summary: string;
  outcome_date: string;
  net_return: string;
  excess_return: string;
  direction_hit: boolean;
  brier_score: string;
};

export type Lesson = {
  id: string;
  week_ending: string;
  scope: string;
  hypothesis: string;
  confidence: string;
  status: string;
};

export type Challenger = {
  id: string;
  status: string;
  replay_case_count: number;
  shadow_days: number;
  net_excess_return: string;
  champion_excess_return: string;
  max_drawdown: string;
  calibration_score: string;
  created_at: string;
  strategy_version: string;
  champion_version: string;
  differences: {
    changed_rules: string[];
    prompt_overrides: Record<string, unknown>;
    evidence_weights: Record<string, unknown>;
  };
};

export type ActionRunner = (
  label: string,
  operation: () => Promise<unknown>,
) => Promise<void>;

export type BusyActions = ReadonlySet<string>;
