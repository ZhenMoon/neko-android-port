import {
  Page,
  Card,
  Grid,
  Stack,
  Text,
  Tip,
  Warning,
  StatCard,
  StatusBadge,
  DataTable,
  ActionButton,
  Button,
  ButtonGroup,
  KeyValue,
  Divider,
  Input,
  Field,
  Select,
  Toolbar,
  ToolbarGroup,
  RefreshButton,
  EmptyState,
  Alert,
  useToast,
  useDebouncedState,
} from "@neko/plugin-ui"
import type { HostedAction, PluginSurfaceProps } from "@neko/plugin-ui"

type WindowView = {
  pid?: number
  title?: string
  process_name?: string
  hwnd?: number
}

type TargetView = {
  pid?: number
  title?: string
  process_name?: string
}

type DashboardState = {
  platform?: string
  windows_supported?: boolean
  target?: TargetView | null
  focused?: boolean
  allow_unguided?: boolean
  store_enabled?: boolean
  save_screenshots?: boolean
  ocr_available?: boolean
  mss_available?: boolean
  command_require_confirmation?: boolean
  pending_commands?: PendingCommand[]
  audio_available?: boolean
  diary?: DiaryState
  message?: string | null
}

type DiaryState = {
  enabled?: boolean
  date?: string
  dir?: string
  event_count?: number
  counts?: Record<string, number>
  summary?: string
  flushed?: boolean
}

type PendingCommand = {
  token?: string
  command?: string
  shell?: string
  status?: string
  created_at?: number
  output?: string
}

const mouseButtonOptions = [
  { value: "left", label: "Left" },
  { value: "right", label: "Right" },
  { value: "middle", label: "Middle" },
]

const captureModeOptions = [
  { value: "target", label: "Target window" },
  { value: "fullscreen", label: "Fullscreen" },
]

export default function KeyboardControllerPanel(props: PluginSurfaceProps<DashboardState>) {
  const { t, state, actions } = props
  const safeState = state || {}
  const safeActions = Array.isArray(actions) ? (actions as HostedAction[]) : []
  const findWindows = safeActions.find((action) => action.id === "find_windows")
  const setTarget = safeActions.find((action) => action.id === "set_target")
  const clearTarget = safeActions.find((action) => action.id === "clear_target")
  const pressKeys = safeActions.find((action) => action.id === "press_keys")
  const typeText = safeActions.find((action) => action.id === "type_text")
  const mouseMove = safeActions.find((action) => action.id === "mouse_move")
  const mouseClick = safeActions.find((action) => action.id === "mouse_click")
  const capture = safeActions.find((action) => action.id === "capture_screen")
  const saveShot = safeActions.find((action) => action.id === "save_screenshot")
  const analyzeAudio = safeActions.find((action) => action.id === "analyze_audio")
  const confirmCmd = safeActions.find((action) => action.id === "confirm_command")
  const rejectCmd = safeActions.find((action) => action.id === "reject_command")
  const setCommandConfirm = safeActions.find((action) => action.id === "set_command_confirmation")
  const getWindowRect = safeActions.find((action) => action.id === "get_window_rect")
  const clickInWindow = safeActions.find((action) => action.id === "click_in_window")
  const findImage = safeActions.find((action) => action.id === "find_image")
  const diaryWrite = safeActions.find((action) => action.id === "diary_write_now")
  const diaryStatus = safeActions.find((action) => action.id === "diary_status")
  const setDiaryEnabled = safeActions.find((action) => action.id === "set_diary_enabled")
  const diaryRead = safeActions.find((action) => action.id === "diary_read")

  const toast = useToast()
  const [searchQuery, setSearchQuery] = props.useLocalState<string>("searchQuery", "")
  const [searchResults, setSearchResults] = props.useLocalState<WindowView[]>("searchResults", [])
  const [searchMessage, setSearchMessage] = props.useLocalState<string>("searchMessage", "")
  const [pressInput, setPressInput] = props.useLocalState<string>("pressInput", "space")
  const [typeInput, setTypeInput] = props.useLocalState<string>("typeInput", "")
  const [mouseX, setMouseX] = props.useLocalState<string>("mouseX", "")
  const [mouseY, setMouseY] = props.useLocalState<string>("mouseY", "")
  const [mouseButton, setMouseButton] = props.useLocalState<string>("mouseButton", "left")
  const [captureMode, setCaptureMode] = props.useLocalState<string>("captureMode", "target")
  const [captureResult, setCaptureResult] = props.useLocalState<string>("captureResult", "")
  const [audioDuration, setAudioDuration] = props.useLocalState<string>("audioDuration", "4")
  const [audioResult, setAudioResult] = props.useLocalState<string>("audioResult", "")
  const [commandInput, setCommandInput] = props.useLocalState<string>("commandInput", "")
  const [commandOutput, setCommandOutput] = props.useLocalState<string>("commandOutput", "")
  const [winRelX, setWinRelX] = props.useLocalState<string>("winRelX", "")
  const [winRelY, setWinRelY] = props.useLocalState<string>("winRelY", "")
  const [winRectResult, setWinRectResult] = props.useLocalState<string>("winRectResult", "")
  const [imagePath, setImagePath] = props.useLocalState<string>("imagePath", "")
  const [imageMode, setImageMode] = props.useLocalState<string>("imageMode", "target")
  const [imageResult, setImageResult] = props.useLocalState<string>("imageResult", "")
  const [diaryDate, setDiaryDate] = props.useLocalState<string>("diaryDate", "")
  const [diaryReadResult, setDiaryReadResult] = props.useLocalState<string>("diaryReadResult", "")
  const [resultMessage, setResultMessage, debouncedResultMessage] = useDebouncedState("", 2500)

  const target = safeState.target || null
  const windowsSupported = !!safeState.windows_supported

  const setFeedback = (message: string) => {
    setResultMessage(message || "")
  }

  const doFind = async () => {
    setSearchMessage("")
    if (!findWindows) {
      setSearchMessage(t("panel.errors.noFindAction"))
      return
    }
    try {
      const result = await props.api.call("find_windows", { query: searchQuery || "" })
      const windows = Array.isArray(result?.windows) ? result.windows : []
      setSearchResults(windows)
      setSearchMessage(
        windows.length > 0
          ? t("panel.find.found", { count: windows.length, total: result?.total ?? windows.length })
          : t("panel.find.none"),
      )
    } catch (error) {
      setSearchMessage(error && error.message ? error.message : String(error))
      setSearchResults([])
    }
  }

  const doSetTarget = async (win: WindowView) => {
    if (!setTarget) return
    try {
      await props.api.call("set_target", { pid: win.pid })
      setSearchResults([])
      setSearchMessage("")
      setFeedback(t("panel.feedback.targetSet"))
      toast.success(t("panel.feedback.targetSet"))
      await props.api.refresh()
    } catch (error) {
      setFeedback(error && error.message ? error.message : String(error))
    }
  }

  const doClearTarget = async () => {
    if (!clearTarget) return
    try {
      await props.api.call("clear_target", {})
      setFeedback(t("panel.feedback.targetCleared"))
      toast.success(t("panel.feedback.targetCleared"))
      await props.api.refresh()
    } catch (error) {
      setFeedback(error && error.message ? error.message : String(error))
    }
  }

  const doPress = async () => {
    if (!pressKeys) return
    try {
      const result = await props.api.call("press_keys", { keys: pressInput || "" })
      setFeedback(result?.message || t("panel.feedback.pressed"))
      toast.success(result?.message || t("panel.feedback.pressed"))
    } catch (error) {
      setFeedback(error && error.message ? error.message : String(error))
    }
  }

  const doType = async () => {
    if (!typeText) return
    try {
      const result = await props.api.call("type_text", { text: typeInput || "" })
      setFeedback(result?.message || t("panel.feedback.typed"))
      toast.success(result?.message || t("panel.feedback.typed"))
    } catch (error) {
      setFeedback(error && error.message ? error.message : String(error))
    }
  }

  const doMouseMove = async () => {
    if (!mouseMove) return
    try {
      const result = await props.api.call("mouse_move", { x: Number(mouseX) || 0, y: Number(mouseY) || 0 })
      setFeedback(result?.message || t("panel.feedback.moved"))
    } catch (error) {
      setFeedback(error && error.message ? error.message : String(error))
    }
  }

  const doMouseClick = async () => {
    if (!mouseClick) return
    try {
      const result = await props.api.call("mouse_click", {
        x: Number(mouseX) || 0,
        y: Number(mouseY) || 0,
        button: mouseButton,
      })
      setFeedback(result?.message || t("panel.feedback.clicked"))
      toast.success(result?.message || t("panel.feedback.clicked"))
    } catch (error) {
      setFeedback(error && error.message ? error.message : String(error))
    }
  }

  const doCapture = async () => {
    if (!capture) return
    setCaptureResult("")
    try {
      const result = await props.api.call("capture_screen", { mode: captureMode })
      setFeedback(result?.message || t("panel.feedback.captured"))
      const text = String(result?.text || "").trim()
      setCaptureResult(text ? text : t("panel.capture.noText"))
    } catch (error) {
      setFeedback(error && error.message ? error.message : String(error))
    }
  }

  const doSaveShot = async () => {
    if (!saveShot) return
    try {
      const result = await props.api.call("save_screenshot", { mode: captureMode })
      setFeedback(result?.message || t("panel.feedback.shotSaved"))
      toast.success(result?.message || t("panel.feedback.shotSaved"))
    } catch (error) {
      setFeedback(error && error.message ? error.message : String(error))
    }
  }

  const doConfirmCommand = async (token?: string) => {
    if (!confirmCmd || !token) return
    try {
      await props.api.call("confirm_command", { token })
      setFeedback(t("panel.feedback.commandConfirmed"))
      toast.success(t("panel.feedback.commandConfirmed"))
      await props.api.refresh()
    } catch (error) {
      setFeedback(error && error.message ? error.message : String(error))
    }
  }

  const doRejectCommand = async (token?: string) => {
    if (!rejectCmd || !token) return
    try {
      await props.api.call("reject_command", { token })
      setFeedback(t("panel.feedback.commandRejected"))
      toast.success(t("panel.feedback.commandRejected"))
      await props.api.refresh()
    } catch (error) {
      setFeedback(error && error.message ? error.message : String(error))
    }
  }

  const doSetCommandConfirm = async (enabled: boolean) => {
    if (!setCommandConfirm) return
    try {
      const result = await props.api.call("set_command_confirmation", { enabled })
      setFeedback(result?.message || t("panel.command.confirmToggled"))
      toast.success(result?.message || t("panel.command.confirmToggled"))
      await props.api.refresh()
    } catch (error) {
      setFeedback(error && error.message ? error.message : String(error))
    }
  }

  const doRunCommand = async () => {
    const cmd = String(commandInput || "").trim()
    if (!cmd) {
      setFeedback(t("panel.command.runEmpty"))
      return
    }
    setCommandOutput("")
    try {
      const result = await props.api.call("run_command", { command: cmd })
      const out = String(result?.output || result?.message || "")
      setCommandOutput(out)
      setFeedback(t("panel.command.runDone"))
      toast.success(t("panel.command.runDone"))
      if (result?.status === "awaiting_confirmation") {
        await props.api.refresh()
      }
    } catch (error) {
      setCommandOutput("")
      setFeedback(error && error.message ? error.message : String(error))
    }
  }

  const doGetWindowRect = async () => {
    if (!getWindowRect) return
    setWinRectResult("")
    try {
      const result = await props.api.call("get_window_rect", {})
      const w = result?.window_rect
      const c = result?.client_rect
      if (w) {
        const cw = c?.width ?? w.right - w.left
        const ch = c?.height ?? w.bottom - w.top
        setWinRectResult(t("panel.rect.result", { x: w.left, y: w.top, w: cw, h: ch }))
      } else {
        setWinRectResult(String(result?.message || ""))
      }
    } catch (error) {
      setWinRectResult(error && error.message ? error.message : String(error))
    }
  }

  const doClickInWindow = async () => {
    if (!clickInWindow) return
    const x = Number(winRelX) || 0
    const y = Number(winRelY) || 0
    try {
      const result = await props.api.call("click_in_window", { x, y })
      setFeedback(result?.message || t("panel.clickInWindow.done"))
      toast.success(result?.message || t("panel.clickInWindow.done"))
    } catch (error) {
      setFeedback(error && error.message ? error.message : String(error))
    }
  }

  const doFindImage = async () => {
    if (!findImage) return
    const path = String(imagePath || "").trim()
    if (!path) {
      setFeedback(t("panel.findImage.needPath"))
      return
    }
    setImageResult("")
    try {
      const result = await props.api.call("find_image", { template_path: path, mode: imageMode })
      const matches = Array.isArray(result?.matches) ? result.matches : []
      if (matches.length === 0) {
        setImageResult(t("panel.findImage.noMatch"))
      } else {
        const lines = matches.map((mm: { x?: number; y?: number; score?: number }) =>
          `(${mm.x}, ${mm.y}) score=${mm.score ?? ""}`
        )
        setImageResult(lines.join("\n"))
      }
    } catch (error) {
      setImageResult(error && error.message ? error.message : String(error))
    }
  }

  const doAnalyzeAudio = async () => {
    if (!analyzeAudio) return
    setAudioResult("")
    try {
      const duration = Math.max(1, Math.min(15, Number(audioDuration) || 4))
      const result = await props.api.call("analyze_audio", { duration })
      const interpretation = String(result?.interpretation || "")
      const volume = result?.volume_db != null ? ` | ${result.volume_db} dB` : ""
      const centroid = result?.centroid_hz != null ? ` | centroid ${Math.round(result.centroid_hz)} Hz` : ""
      setAudioResult(interpretation + volume + centroid)
      if (interpretation) setFeedback(interpretation)
    } catch (error) {
      setAudioResult("")
      setFeedback(error && error.message ? error.message : String(error))
    }
  }

  const doDiaryToggle = async () => {
    if (!setDiaryEnabled) return
    try {
      const result = await props.api.call("set_diary_enabled", { enabled: !safeState.diary?.enabled })
      setFeedback(result?.message || "")
      toast.success(result?.message || "")
    } catch (error) {
      setFeedback(error && error.message ? error.message : String(error))
    }
  }

  return (
    <Page title={t("panel.title")} subtitle={t("panel.subtitle")}>
      <Toolbar>
        <ToolbarGroup>
          <StatusBadge tone={windowsSupported ? "success" : "danger"}>
            {windowsSupported ? t("panel.badges.windowsReady") : t("panel.badges.notWindows")}
          </StatusBadge>
          {target ? (
            <StatusBadge tone={safeState.focused ? "success" : "warning"}>
              {safeState.focused ? t("panel.badges.focused") : t("panel.badges.notFocused")}
            </StatusBadge>
          ) : (
            <StatusBadge tone="warning">{t("panel.badges.noTarget")}</StatusBadge>
          )}
          {safeState.allow_unguided ? <StatusBadge tone="warning">{t("panel.badges.unguided")}</StatusBadge> : null}
          <StatusBadge tone={safeState.ocr_available ? "success" : "warning"}>
            {safeState.ocr_available ? t("panel.badges.ocrReady") : t("panel.badges.ocrMissing")}
          </StatusBadge>
        </ToolbarGroup>
        <ToolbarGroup>
          <RefreshButton>{t("panel.actions.refresh")}</RefreshButton>
        </ToolbarGroup>
      </Toolbar>

      <Grid cols={4}>
        <StatCard label={t("panel.stats.platform")} value={safeState.platform || "unknown"} />
        <StatCard
          label={t("panel.stats.target")}
          value={target ? `${target.title || ""} (${target.pid ?? ""})` : t("panel.stats.noTarget")}
        />
        <StatCard label={t("panel.stats.store")} value={safeState.store_enabled ? t("panel.stats.on") : t("panel.stats.off")} />
        <StatCard label={t("panel.stats.ocr")} value={safeState.ocr_available ? t("panel.stats.on") : t("panel.stats.off")} />
      </Grid>

      {debouncedResultMessage ? <Alert tone="info">{debouncedResultMessage}</Alert> : null}

      <Grid cols={2}>
        <Card title={t("panel.target.title")}>
          <Stack>
            {target ? (
              <>
                <KeyValue
                  items={[
                    { key: "pid", label: "PID", value: target.pid ?? "—" },
                    { key: "title", label: t("panel.target.titleLabel"), value: target.title || "—" },
                    { key: "process", label: t("panel.target.process"), value: target.process_name || "—" },
                  ]}
                />
                <ButtonGroup>
                  {clearTarget ? <Button tone="danger" onClick={doClearTarget}>{t("panel.target.clear")}</Button> : null}
                </ButtonGroup>
              </>
            ) : (
              <EmptyState
                title={t("panel.target.empty.title")}
                description={t("panel.target.empty.description")}
              />
            )}
          </Stack>
        </Card>

        <Card title={t("panel.find.title")}>
          <Stack>
            <Field label={t("panel.find.query")} help={t("panel.find.help")}>
              <Input value={searchQuery} placeholder={t("panel.find.placeholder")} onChange={setSearchQuery} />
            </Field>
            <Button tone="primary" onClick={doFind}>{t("panel.find.submit")}</Button>
            {searchMessage ? <Text>{searchMessage}</Text> : null}
            {searchResults.length > 0 ? (
              <>
                <DataTable
                  data={searchResults}
                  rowKey="pid"
                  columns={[
                    { key: "pid", label: "PID" },
                    { key: "title", label: t("panel.find.colTitle") },
                    { key: "process_name", label: t("panel.find.colProcess") },
                    {
                      key: "set",
                      label: "",
                      render: (row) =>
                        setTarget ? (
                          <Button tone="success" onClick={() => doSetTarget(row as WindowView)}>
                            {t("panel.find.set")}
                          </Button>
                        ) : null,
                    },
                  ]}
                />
                <Tip>{t("panel.find.tip")}</Tip>
              </>
            ) : null}
          </Stack>
        </Card>
      </Grid>

      <Grid cols={2}>
        <Card title={t("panel.input.title")}>
          <Stack>
            <Field label={t("panel.input.pressLabel")} help={t("panel.input.pressHelp")}>
              <Input value={pressInput} placeholder="ctrl+c" onChange={setPressInput} />
            </Field>
            {pressKeys ? (
              <ActionButton action={pressKeys} values={{ keys: pressInput }}>
                {t("panel.input.press")}
              </ActionButton>
            ) : null}

            <Divider />

            <Field label={t("panel.input.typeLabel")} help={t("panel.input.typeHelp")}>
              <Input value={typeInput} placeholder="Hello" onChange={setTypeInput} />
            </Field>
            {typeText ? (
              <ActionButton action={typeText} values={{ text: typeInput }}>
                {t("panel.input.type")}
              </ActionButton>
            ) : null}
          </Stack>
        </Card>

        <Card title={t("panel.mouse.title")}>
          <Stack>
            <Grid cols={2}>
              <Field label="X">
                <Input value={mouseX} placeholder="960" onChange={setMouseX} />
              </Field>
              <Field label="Y">
                <Input value={mouseY} placeholder="540" onChange={setMouseY} />
              </Field>
            </Grid>
            <Field label={t("panel.mouse.button")}>
              <Select value={mouseButton} options={mouseButtonOptions} onChange={setMouseButton} />
            </Field>
            <ButtonGroup>
              {mouseMove ? (
                <ActionButton action={mouseMove} values={{ x: Number(mouseX) || 0, y: Number(mouseY) || 0 }}>
                  {t("panel.mouse.move")}
                </ActionButton>
              ) : null}
              {mouseClick ? (
                <ActionButton
                  action={mouseClick}
                  values={{ x: Number(mouseX) || 0, y: Number(mouseY) || 0, button: mouseButton }}
                >
                  {t("panel.mouse.click")}
                </ActionButton>
              ) : null}
            </ButtonGroup>
            <Text>{t("panel.mouse.coordNote")}</Text>
          </Stack>
        </Card>
      </Grid>

      <Card title={t("panel.capture.title")}>
        <Stack>
          <Field label={t("panel.capture.mode")} help={t("panel.capture.help")}>
            <Select value={captureMode} options={captureModeOptions} onChange={setCaptureMode} />
          </Field>
          <ButtonGroup>
            {capture ? (
              <ActionButton action={capture} values={{ mode: captureMode }}>
                {t("panel.capture.run")}
              </ActionButton>
            ) : null}
            {saveShot ? (
              <ActionButton action={saveShot} values={{ mode: captureMode }}>
                {t("panel.capture.save")}
              </ActionButton>
            ) : null}
          </ButtonGroup>
          {captureResult ? (
            <>
              <Divider />
              <Text>{t("panel.capture.ocrResult")}</Text>
              <pre className="neko-pre">{captureResult}</pre>
            </>
          ) : null}
          <Tip>{t("panel.capture.tip")}</Tip>
        </Stack>
      </Card>

      <Card title={t("panel.rect.title")}>
        <Stack>
          <ButtonGroup>
            {getWindowRect ? (
              <Button tone="primary" onClick={doGetWindowRect}>
                {t("panel.rect.get")}
              </Button>
            ) : null}
          </ButtonGroup>
          {winRectResult ? (
            <>
              <Divider />
              <pre className="neko-pre">{winRectResult}</pre>
            </>
          ) : null}
          <Divider />
          <Grid cols={2}>
            <Field label={t("panel.rect.relX")}>
              <Input value={winRelX} placeholder="0" onChange={setWinRelX} />
            </Field>
            <Field label={t("panel.rect.relY")}>
              <Input value={winRelY} placeholder="0" onChange={setWinRelY} />
            </Field>
          </Grid>
          <ButtonGroup>
            {clickInWindow ? (
              <Button tone="success" onClick={doClickInWindow}>
                {t("panel.rect.clickRel")}
              </Button>
            ) : null}
          </ButtonGroup>
          <Tip>{t("panel.rect.tip")}</Tip>
        </Stack>
      </Card>

      <Card title={t("panel.findImage.title")}>
        <Stack>
          <Field label={t("panel.findImage.path")} help={t("panel.findImage.pathHelp")}>
            <Input value={imagePath} placeholder="icon.png" onChange={setImagePath} />
          </Field>
          <Field label={t("panel.findImage.mode")}>
            <Select value={imageMode} options={captureModeOptions} onChange={setImageMode} />
          </Field>
          <ButtonGroup>
            {findImage ? (
              <Button tone="primary" onClick={doFindImage}>
                {t("panel.findImage.run")}
              </Button>
            ) : null}
          </ButtonGroup>
          {imageResult ? (
            <>
              <Divider />
              <Text>{t("panel.findImage.result")}</Text>
              <pre className="neko-pre">{imageResult}</pre>
            </>
          ) : null}
          <Tip>{t("panel.findImage.tip")}</Tip>
        </Stack>
      </Card>

      <Card title={t("panel.diary.title")}>
        <Stack>
          <StatusBadge tone={safeState.diary?.enabled ? "success" : "warning"}>
            {safeState.diary?.enabled ? t("panel.diary.on") : t("panel.diary.off")}
          </StatusBadge>
          <KeyValue
            items={[
              { label: t("panel.diary.date"), value: safeState.diary?.date || "-" },
              { label: t("panel.diary.eventCount"), value: String(safeState.diary?.event_count ?? 0) },
              { label: t("panel.diary.summary"), value: safeState.diary?.summary || t("panel.diary.noEvents") },
            ]}
          />
          <ButtonGroup>
            {setDiaryEnabled ? (
              <Button
                tone={safeState.diary?.enabled ? "default" : "success"}
                onClick={doDiaryToggle}
              >
                {safeState.diary?.enabled ? t("panel.diary.turnOff") : t("panel.diary.turnOn")}
              </Button>
            ) : null}
            {diaryWrite ? (
              <ActionButton
                action={diaryWrite}
                refresh={false}
                onResult={(result: any) => {
                  const message = result?.message || (result?.written ? t("panel.diary.written") : t("panel.diary.noEvents"))
                  setFeedback(message)
                  if (result?.written) toast.success(message)
                }}
                onError={(error) => setFeedback(error?.message ? error.message : String(error))}
              >
                {t("panel.diary.writeNow")}
              </ActionButton>
            ) : null}
          </ButtonGroup>
          <Divider />
          <Field label={t("panel.diary.readDate")} help={t("panel.diary.readDateHelp")}>
            <Input value={diaryDate} placeholder={t("panel.diary.datePlaceholder")} onChange={setDiaryDate} />
          </Field>
          <ButtonGroup>
          {diaryRead ? (
            <ActionButton
              action={diaryRead}
              values={{ date: diaryDate }}
              onResult={(result: any) => {
                setDiaryReadResult(String(result?.markdown || result?.message || ""))
                setFeedback(result?.message || t("panel.diary.readResult"))
              }}
              onError={(error) => {
                setDiaryReadResult("")
                setFeedback(error?.message ? error.message : String(error))
              }}
            >
              {t("panel.diary.read")}
            </ActionButton>
          ) : null}
          </ButtonGroup>
          {diaryReadResult ? (
            <>
              <Divider />
              <Text>{t("panel.diary.readResult")}</Text>
              <pre className="neko-pre">{diaryReadResult}</pre>
            </>
          ) : null}
          <Tip>{t("panel.diary.tip")}</Tip>
        </Stack>
      </Card>

      <Card title={t("panel.command.title")}>
        <Stack>
          <StatusBadge tone={safeState.command_require_confirmation ? "warning" : "success"}>
            {safeState.command_require_confirmation ? t("panel.command.confirmEnabled") : t("panel.command.confirmDisabled")}
          </StatusBadge>
          <ButtonGroup>
            {setCommandConfirm ? (
              <>
                <Button
                  tone={safeState.command_require_confirmation ? "default" : "success"}
                  onClick={() => doSetCommandConfirm(true)}
                >
                  {t("panel.command.turnOn")}
                </Button>
                <Button
                  tone={safeState.command_require_confirmation ? "danger" : "default"}
                  onClick={() => doSetCommandConfirm(false)}
                >
                  {t("panel.command.turnOff")}
                </Button>
              </>
            ) : null}
          </ButtonGroup>
          <Field label={t("panel.command.runLabel")} help={t("panel.command.runHelp")}>
            <Input value={commandInput} placeholder="dir /b" onChange={setCommandInput} />
          </Field>
          <ButtonGroup>
            <Button tone="primary" onClick={doRunCommand}>
              {t("panel.command.run")}
            </Button>
          </ButtonGroup>
          {commandOutput ? (
            <>
              <Divider />
              <Text>{t("panel.command.runOutput")}</Text>
              <pre className="neko-pre">{commandOutput}</pre>
            </>
          ) : null}
          {safeState.pending_commands && safeState.pending_commands.length > 0 ? (
            <DataTable
              data={safeState.pending_commands}
              rowKey="token"
              columns={[
                { key: "token", label: t("panel.command.token") },
                { key: "command", label: t("panel.command.command") },
                {
                  key: "status",
                  label: t("panel.command.status"),
                  render: (row) => {
                    const status = String(row.status || "pending")
                    const labels: Record<string, string> = {
                      pending: t("panel.command.statusPending"),
                      running: t("panel.command.statusRunning"),
                      done: t("panel.command.statusDone"),
                      failed: t("panel.command.statusFailed"),
                      rejected: t("panel.command.statusRejected"),
                    }
                    return labels[status] || status
                  },
                },
                {
                  key: "actions",
                  label: "",
                  render: (row) => {
                    const token = String(row.token || "")
                    if (String(row.status) === "pending") {
                      return (
                        <ButtonGroup>
                          {confirmCmd ? (
                            <Button tone="success" onClick={() => doConfirmCommand(token)}>
                              {t("panel.command.confirm")}
                            </Button>
                          ) : null}
                          {rejectCmd ? (
                            <Button tone="danger" onClick={() => doRejectCommand(token)}>
                              {t("panel.command.reject")}
                            </Button>
                          ) : null}
                        </ButtonGroup>
                      )
                    }
                    if (row.status === "done" && row.output) {
                      return <pre className="neko-pre">{row.output}</pre>
                    }
                    return null
                  },
                },
              ]}
            />
          ) : (
            <EmptyState title={t("panel.command.empty")} />
          )}
          <Tip>{t("panel.command.tip")}</Tip>
        </Stack>
      </Card>

      <Card title={t("panel.audio.title")}>
        <Stack>
          <StatusBadge tone={safeState.audio_available ? "success" : "warning"}>
            {safeState.audio_available ? t("panel.audio.badge") : t("panel.audio.badgeMissing")}
          </StatusBadge>
          <Field label={t("panel.audio.duration")} help={t("panel.audio.help")}>
            <Input value={audioDuration} placeholder="4" onChange={setAudioDuration} />
          </Field>
          <ButtonGroup>
            {analyzeAudio ? (
              <ActionButton action={analyzeAudio} values={{ duration: Math.max(1, Math.min(15, Number(audioDuration) || 4)) }}>
                {t("panel.audio.run")}
              </ActionButton>
            ) : null}
          </ButtonGroup>
          {audioResult ? (
            <>
              <Divider />
              <Text>{t("panel.audio.result")}</Text>
              <pre className="neko-pre">{audioResult}</pre>
            </>
          ) : null}
          <Tip>{t("panel.audio.tip")}</Tip>
        </Stack>
      </Card>

      <Warning>{t("panel.warnings.safety")}</Warning>
    </Page>
  )
}
