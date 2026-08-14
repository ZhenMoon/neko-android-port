import {
  Page,
  Card,
  Grid,
  Stack,
  Text,
  StatusBadge,
  DataTable,
  ActionButton,
  Button,
  ButtonGroup,
  Field,
  Input,
  Textarea,
  Divider,
  RefreshButton,
  EmptyState,
  Alert,
  useToast,
} from "@neko/plugin-ui"
import type { HostedAction, PluginSurfaceProps } from "@neko/plugin-ui"

type LlmState = {
  llm_url?: string
  llm_model?: string
  llm_api_key?: string
  vision_url?: string
  vision_model?: string
  vision_api_key?: string
  text_configured?: boolean
  vision_configured?: boolean
}

type GameView = {
  game_id?: string
  name?: string
  has_profile?: boolean
  has_draft?: boolean
  operation_count?: number
}

type SessionView = {
  session_id?: string
  goal?: string
  status?: string
  steps?: number
  last_ocr?: string
}

type DashboardState = {
  llm?: LlmState
  search_available?: boolean
  search_server?: string
  allow_python?: boolean
  games?: GameView[]
  sessions?: SessionView[]
}

export default function GameBrainPanel(props: PluginSurfaceProps<DashboardState>) {
  const { t, state, actions } = props
  const safe = state || {}
  const safeActions = Array.isArray(actions) ? (actions as HostedAction[]) : []

  const saveLlm = safeActions.find((a) => a.id === "save_llm_config")
  const testLlm = safeActions.find((a) => a.id === "test_llm_config")
  const learn = safeActions.find((a) => a.id === "learn_game")
  const confirmGame = safeActions.find((a) => a.id === "confirm_game")
  const genOps = safeActions.find((a) => a.id === "generate_operations")
  const play = safeActions.find((a) => a.id === "play")

  const toast = useToast()
  const [llmUrl, setLlmUrl] = props.useLocalState<string>("llmUrl", safe.llm?.llm_url || "")
  const [llmKey, setLlmKey] = props.useLocalState<string>("llmKey", "")
  const [llmModel, setLlmModel] = props.useLocalState<string>("llmModel", safe.llm?.llm_model || "")
  const [visUrl, setVisUrl] = props.useLocalState<string>("visUrl", safe.llm?.vision_url || "")
  const [visKey, setVisKey] = props.useLocalState<string>("visKey", "")
  const [visModel, setVisModel] = props.useLocalState<string>("visModel", safe.llm?.vision_model || "")
  const [learnName, setLearnName] = props.useLocalState<string>("learnName", "")
  const [learnWin, setLearnWin] = props.useLocalState<string>("learnWin", "")
  const [playGoal, setPlayGoal] = props.useLocalState<string>("playGoal", "")
  const [selectedGame, setSelectedGame] = props.useLocalState<string>("selectedGame", "")
  const [testResult, setTestResult] = props.useLocalState<string>("testResult", "")

  const games = safe.games || []
  const sessions = safe.sessions || []

  const handleSaveLlm = async () => {
    if (!saveLlm) return
    try {
      await saveLlm.call({
        llm_url: llmUrl,
        llm_api_key: llmKey,
        llm_model: llmModel,
        vision_url: visUrl,
        vision_api_key: visKey,
        vision_model: visModel,
      })
      toast.success(t("panel.llm.saved"))
    } catch (e) {
      toast.error(String(e))
    }
  }

  const handleTestLlm = async () => {
    if (!testLlm) return
    try {
      const r = await testLlm.call({})
      setTestResult(JSON.stringify(r, null, 2))
    } catch (e) {
      toast.error(String(e))
    }
  }

  const handleLearn = async () => {
    if (!learn || !learnName.trim()) return
    try {
      const r = await learn.call({ game_name: learnName.trim(), window_keywords: learnWin.trim() })
      const draft = r && (r as any).draft
      toast.success(t("panel.learn.done") + (draft ? ` (${draft.name})` : ""))
    } catch (e) {
      toast.error(String(e))
    }
  }

  const handleConfirm = async (gameId: string) => {
    if (!confirmGame) return
    try {
      await confirmGame.call({ game_id: gameId })
      toast.success(t("panel.confirm.done"))
    } catch (e) {
      toast.error(String(e))
    }
  }

  const handleGenOps = async (gameId: string) => {
    if (!genOps) return
    try {
      const r = await genOps.call({ game_id: gameId })
      toast.success(t("panel.genOps.done") + (r ? ` (${(r as any).count})` : ""))
    } catch (e) {
      toast.error(String(e))
    }
  }

  const handlePlay = async () => {
    if (!play || !selectedGame || !playGoal.trim()) return
    try {
      const r = await play.call({ game_id: selectedGame, goal: playGoal.trim() })
      toast.success(t("panel.play.started") + (r ? ` (${(r as any).session_id})` : ""))
      setPlayGoal("")
    } catch (e) {
      toast.error(String(e))
    }
  }

  const llm = safe.llm || {}
  const llmBadge = llm.text_configured ? (
    <StatusBadge tone="success">{t("panel.llm.ready")}</StatusBadge>
  ) : (
    <StatusBadge tone="danger">{t("panel.llm.missing")}</StatusBadge>
  )
  const visBadge = llm.vision_configured ? (
    <StatusBadge tone="success">{t("panel.llm.visionReady")}</StatusBadge>
  ) : (
    <StatusBadge tone="warning">{t("panel.llm.visionMissing")}</StatusBadge>
  )

  return (
    <Page title={props.plugin.name} subtitle={t("panel.subtitle")}>
      <Grid>
        <Card title={t("panel.llm.title")} actions={[llmBadge, visBadge]}>
          <Stack>
            <Text>{t("panel.llm.help")}</Text>
            <Field label={t("panel.llm.url")}>
              <Input value={llmUrl} onChange={setLlmUrl} placeholder="https://api.deepseek.com" />
            </Field>
            <Field label={t("panel.llm.apiKey")}>
              <Input value={llmKey} onChange={setLlmKey} placeholder="sk-..." type="password" />
            </Field>
            <Field label={t("panel.llm.model")}>
              <Input value={llmModel} onChange={setLlmModel} placeholder="deepseek-v4-flash" />
            </Field>
            <Divider />
            <Text>{t("panel.llm.visionHelp")}</Text>
            <Field label={t("panel.llm.visionUrl")}>
              <Input value={visUrl} onChange={setVisUrl} placeholder="https://api.siliconflow.cn/v1" />
            </Field>
            <Field label={t("panel.llm.visionApiKey")}>
              <Input value={visKey} onChange={setVisKey} placeholder="sk-..." type="password" />
            </Field>
            <Field label={t("panel.llm.visionModel")}>
              <Input value={visModel} onChange={setVisModel} placeholder="Qwen/Qwen3.5-397B-A17B" />
            </Field>
            <ButtonGroup>
              {saveLlm ? (
                <ActionButton action={saveLlm} onClick={handleSaveLlm}>
                  {t("panel.llm.save")}
                </ActionButton>
              ) : null}
              {testLlm ? (
                <Button onClick={handleTestLlm} tone="secondary">
                  {t("panel.llm.test")}
                </Button>
              ) : null}
            </ButtonGroup>
            {testResult ? (
              <Textarea readOnly value={testResult} rows={5} />
            ) : null}
          </Stack>
        </Card>

        <Card title={t("panel.learn.title")}>
          <Stack>
            <Text>{t("panel.learn.help")}</Text>
            <Field label={t("panel.learn.name")}>
              <Input value={learnName} onChange={setLearnName} placeholder={t("panel.learn.namePh")} />
            </Field>
            <Field label={t("panel.learn.window")}>
              <Input value={learnWin} onChange={setLearnWin} placeholder={t("panel.learn.windowPh")} />
            </Field>
            {learn ? (
              <ActionButton action={learn} onClick={handleLearn}>
                {t("panel.learn.run")}
              </ActionButton>
            ) : null}
            <Text>{t("panel.learn.tip")}</Text>
          </Stack>
        </Card>
      </Grid>

      <Card title={t("panel.games.title")}>
        <Stack>
          <Text>{t("panel.games.help")}</Text>
          {games.length === 0 ? (
            <EmptyState title={t("panel.games.empty")} />
          ) : (
            <DataTable
              data={games}
              rowKey="game_id"
              columns={[
                { key: "name", label: t("panel.games.name") },
                { key: "game_id", label: "ID" },
                {
                  key: "has_profile",
                  label: t("panel.games.status"),
                  render: (row) => (row.has_profile ? "✓ 已确认" : row.has_draft ? "草稿" : "-"),
                },
                { key: "operation_count", label: t("panel.games.ops") },
              ]}
            />
          )}
          {games.map((g) => (
            <Stack key={g.game_id} direction="row">
              <Text>{g.name}</Text>
              {g.has_profile ? (
                <Button onClick={() => handleGenOps(g.game_id as string)} tone="secondary">
                  {t("panel.games.genOps")}
                </Button>
              ) : g.has_draft ? (
                <Button onClick={() => handleConfirm(g.game_id as string)} tone="primary">
                  {t("panel.games.confirm")}
                </Button>
              ) : null}
            </Stack>
          ))}
        </Stack>
      </Card>

      <Card title={t("panel.play.title")}>
        <Stack>
          <Text>{t("panel.play.help")}</Text>
          <Field label={t("panel.play.game")}>
            <select
              value={selectedGame}
              onChange={(e) => setSelectedGame(e.target.value)}
              style={{ width: "100%" }}
            >
              <option value="">{t("panel.play.select")}</option>
              {games.filter((g) => g.has_profile).map((g) => (
                <option key={g.game_id} value={g.game_id}>
                  {g.name}
                </option>
              ))}
            </select>
          </Field>
          <Field label={t("panel.play.goal")}>
            <Input value={playGoal} onChange={setPlayGoal} placeholder={t("panel.play.goalPh")} />
          </Field>
          {play ? (
            <ActionButton action={play} onClick={handlePlay}>
              {t("panel.play.run")}
            </ActionButton>
          ) : null}
        </Stack>
      </Card>

      <Card title={t("panel.sessions.title")}>
        <Stack>
          {sessions.length === 0 ? (
            <EmptyState title={t("panel.sessions.empty")} />
          ) : (
            <DataTable
              data={sessions}
              rowKey="session_id"
              columns={[
                { key: "session_id", label: "ID" },
                { key: "goal", label: t("panel.sessions.goal") },
                { key: "status", label: t("panel.sessions.status") },
                { key: "steps", label: t("panel.sessions.steps") },
                {
                  key: "last_ocr",
                  label: t("panel.sessions.ocr"),
                  render: (row) => (row.last_ocr ? String(row.last_ocr).slice(0, 80) : "-"),
                },
              ]}
            />
          )}
        </Stack>
      </Card>
    </Page>
  )
}
