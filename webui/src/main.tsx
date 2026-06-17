import React, {useCallback, useEffect, useMemo, useRef, useState} from "react";
import {createRoot} from "react-dom/client";
import {
  Alert,
  Button,
  Card,
  Chip,
  Description,
  Form,
  Input,
  Label,
  Spinner,
  Switch,
  Table,
  Tabs,
  TextField,
} from "@heroui/react";
import {ChartTooltip} from "@heroui-pro/react/chart-tooltip";
import {KPI} from "@heroui-pro/react/kpi";
import {LineChart} from "@heroui-pro/react/line-chart";
import {NativeSelect} from "@heroui-pro/react/native-select";
import {
  Bell,
  ChartLine,
  Check,
  CircleCheck,
  CircleExclamation,
  Copy,
  Database,
  FloppyDisk,
  Gear,
  House,
  Key,
  ListCheck,
  Magnifier,
  Plus,
  Persons,
  Play,
  ShieldCheck,
  TrashBin,
} from "@gravity-ui/icons";

import "./styles.css";

declare global {
  interface Window {
    AstrBotPluginPage?: {
      ready: () => Promise<void>;
      apiGet: (endpoint: string) => Promise<ApiResponse>;
      apiPost: (endpoint: string, body?: unknown) => Promise<ApiResponse>;
    };
  }
}

type ApiResponse =
  | {status?: "ok"; ok?: true; data?: unknown; [key: string]: unknown}
  | {status?: "error"; ok?: false; message?: string; code?: number; [key: string]: unknown};

type ChatType = "private" | "group";

interface SessionRecord {
  umo: string;
  chat_type: ChatType;
  session_id?: string;
  display_name?: string;
}

interface RoomRef {
  area_id: string;
  area_name?: string;
  building_code: string;
  building_name?: string;
  floor_code: string;
  floor_name?: string;
  room_code: string;
  room_name?: string;
  display_name?: string;
}

interface Subscription {
  id: number;
  umo: string;
  alias: string;
  room: RoomRef;
  room_key?: string;
  threshold: string;
  unit: string;
  interval_seconds: number;
  enabled: boolean;
  alerted?: boolean;
  latest_value?: string | number | null;
  latest_balance?: string | number | null;
  latest_at?: number | null;
  last_error?: string;
}

interface Diagnostic {
  id?: number;
  created_at: number;
  scope: string;
  level: string;
  message: string;
}

interface CredentialState {
  configured?: boolean;
  shiro_jid_masked?: string;
  ym_id_configured?: boolean;
  ym_id_masked?: string;
  state?: "valid" | "expired" | "unknown" | "unconfigured" | string;
  error?: string;
}

interface BootstrapData {
  revision: number;
  sessions: SessionRecord[];
  subscriptions: Subscription[];
  diagnostics: Diagnostic[];
  credentials: CredentialState;
  admin_notice_umo?: string;
}

interface LocationItem {
  code: string;
  name: string;
}

interface Reading {
  captured_at: number;
  value: string | number;
  balance?: string | number | null;
}

interface QueryReportItem {
  alias: string;
  value: string | number;
  unit: string;
  balance?: string | number | null;
  room_name?: string;
  captured_at?: number;
}

interface QueryReport {
  items?: QueryReportItem[];
  errors?: string[];
}

interface EditorState {
  subscriptionId: string;
  alias: string;
  threshold: string;
  unit: string;
  intervalMinutes: string;
  enabled: boolean;
  manualEnabled: boolean;
  manualAreaId: string;
  manualBuildingCode: string;
  manualFloorCode: string;
  manualRoomCode: string;
  manualRoomName: string;
}

type TabKey = "subscriptions" | "credentials" | "history" | "diagnostics";
type ToastKind = "success" | "error";

const emptyEditor: EditorState = {
  subscriptionId: "",
  alias: "",
  threshold: "20",
  unit: "度",
  intervalMinutes: "15",
  enabled: true,
  manualEnabled: false,
  manualAreaId: "",
  manualBuildingCode: "",
  manualFloorCode: "",
  manualRoomCode: "",
  manualRoomName: "",
};

function unwrap<T>(response: ApiResponse): T {
  if (response?.status === "error" || response?.ok === false) {
    const error = new Error(response.message || "接口请求失败。") as Error & {code?: number};
    error.code = response.code;
    throw error;
  }
  return ((response as {data?: T}).data ?? response) as T;
}

function formatTime(timestamp?: number | null): string {
  return timestamp ? new Date(timestamp * 1000).toLocaleString() : "-";
}

function numericValue(value: unknown): number {
  if (value === null || value === undefined || value === "") return Number.POSITIVE_INFINITY;
  const number = Number(value);
  return Number.isFinite(number) ? number : Number.POSITIVE_INFINITY;
}

function sortedSubscriptions(items: Subscription[]): Subscription[] {
  return [...items].sort((a, b) => {
    const aValue = numericValue(a.latest_value);
    const bValue = numericValue(b.latest_value);
    if (aValue !== bValue) return aValue - bValue;
    if (Boolean(a.last_error) !== Boolean(b.last_error)) return a.last_error ? 1 : -1;
    return String(a.alias).localeCompare(String(b.alias), "zh-CN");
  });
}

function sessionLabel(session?: SessionRecord): string {
  if (!session) return "";
  const type = session.chat_type === "group" ? "群聊" : "私聊";
  const id = session.session_id || session.umo;
  const name = String(session.display_name || "").trim();
  const synthetic = `${type} ${id}`;
  if (!name || name === synthetic) return `${type} · ${id}`;
  if (name.startsWith(`${type} `)) return `${type} · ${name.slice(type.length + 1)}`;
  return `${type} · ${name} (${id})`;
}

function bestSessionUmo(sessions: SessionRecord[], subscriptions: Subscription[]): string {
  if (!subscriptions.length) return sessions[0]?.umo || "";
  return sortedSubscriptions(subscriptions)[0]?.umo || sessions[0]?.umo || "";
}

function roomTitle(room: RoomRef): string {
  return (
    room.display_name ||
    [room.area_name, room.building_name, room.floor_name, room.room_name]
      .filter(Boolean)
      .join(" / ") ||
    room.room_name ||
    room.room_code
  );
}

function uniqueAlias(base: string, rows: Subscription[]): string {
  const used = new Set(rows.map((item) => item.alias.toLowerCase()));
  if (!used.has(base.toLowerCase())) return base;
  for (let index = 2; index < 100; index += 1) {
    const candidate = `${base} ${index}`;
    if (!used.has(candidate.toLowerCase())) return candidate;
  }
  return `${base} ${Date.now()}`;
}

function OptionList({items, placeholder}: {items: LocationItem[]; placeholder: string}) {
  return (
    <>
      <NativeSelect.Option value="">{placeholder}</NativeSelect.Option>
      {items.map((item) => (
        <NativeSelect.Option key={String(item.code)} value={String(item.code)}>
          {item.name}
        </NativeSelect.Option>
      ))}
      <NativeSelect.Indicator />
    </>
  );
}

function SelectField({
  label,
  value,
  onChange,
  children,
  description,
  disabled,
}: {
  label: string;
  value: string;
  onChange: (value: string) => void;
  children: React.ReactNode;
  description?: string;
  disabled?: boolean;
}) {
  return (
    <NativeSelect fullWidth>
      <Label>{label}</Label>
      <NativeSelect.Trigger
        disabled={disabled}
        value={value}
        onChange={(event) => onChange(event.target.value)}
      >
        {children}
      </NativeSelect.Trigger>
      {description ? <Description>{description}</Description> : null}
    </NativeSelect>
  );
}

function TextInput({
  label,
  value,
  onChange,
  placeholder,
  type = "text",
  description,
  disabled,
  maxLength,
}: {
  label: string;
  value: string;
  onChange: (value: string) => void;
  placeholder?: string;
  type?: string;
  description?: string;
  disabled?: boolean;
  maxLength?: number;
}) {
  return (
    <TextField fullWidth isDisabled={disabled} type={type} value={value} onChange={onChange}>
      <Label>{label}</Label>
      <Input maxLength={maxLength} placeholder={placeholder} variant="secondary" />
      {description ? <Description>{description}</Description> : null}
    </TextField>
  );
}

function StatusChip({subscription}: {subscription: Subscription}) {
  if (!subscription.enabled) {
    return (
      <Chip className="whitespace-nowrap" size="sm" variant="soft">
        已停用
      </Chip>
    );
  }
  if (subscription.last_error) {
    return (
      <Chip className="whitespace-nowrap" color="danger" size="sm" variant="soft">
        异常
      </Chip>
    );
  }
  return (
    <Chip className="whitespace-nowrap" color="success" size="sm" variant="soft">
      监控中
    </Chip>
  );
}

function levelColor(level: string): "default" | "success" | "warning" | "danger" | "accent" {
  const normalized = String(level || "info").toLowerCase();
  if (normalized === "error" || normalized === "danger") return "danger";
  if (normalized === "warning" || normalized === "warn") return "warning";
  if (normalized === "success" || normalized === "ok") return "success";
  return "accent";
}

function credentialLabel(credentials: CredentialState): string {
  const labels: Record<string, string> = {
    valid: "有效",
    expired: "已过期，后台查询已暂停",
    unknown: "已配置，尚未验证",
    unconfigured: "未配置",
  };
  const state = String(credentials.state || "unknown");
  return labels[state] || state;
}

function App() {
  const bridgeRef = useRef<Window["AstrBotPluginPage"] | null>(null);
  const [revision, setRevision] = useState(0);
  const [sessions, setSessions] = useState<SessionRecord[]>([]);
  const [subscriptions, setSubscriptions] = useState<Subscription[]>([]);
  const [diagnostics, setDiagnostics] = useState<Diagnostic[]>([]);
  const [credentials, setCredentials] = useState<CredentialState>({state: "unknown"});
  const [adminNoticeUmo, setAdminNoticeUmo] = useState("");
  const [selectedUmo, setSelectedUmo] = useState("");
  const [copySubscriptionId, setCopySubscriptionId] = useState("");
  const [tab, setTab] = useState<TabKey>("subscriptions");
  const [runtimeStatus, setRuntimeStatus] = useState<"connecting" | "ok" | "error">("connecting");
  const [busyAction, setBusyAction] = useState("");
  const [editor, setEditor] = useState<EditorState>(emptyEditor);
  const [editingRoom, setEditingRoom] = useState<RoomRef | null>(null);
  const [areas, setAreas] = useState<LocationItem[]>([]);
  const [buildings, setBuildings] = useState<LocationItem[]>([]);
  const [floors, setFloors] = useState<LocationItem[]>([]);
  const [rooms, setRooms] = useState<LocationItem[]>([]);
  const [roomHint, setRoomHint] = useState("请先配置并验证登录态，再加载校区。");
  const [queryResult, setQueryResult] = useState("尚未查询。");
  const [shiroJID, setShiroJID] = useState("");
  const [ymId, setYmId] = useState("");
  const [historySubscriptionId, setHistorySubscriptionId] = useState("");
  const [historyItems, setHistoryItems] = useState<Reading[]>([]);
  const [historyUnit, setHistoryUnit] = useState("度");
  const [historySummary, setHistorySummary] = useState("请选择订阅并加载历史。");
  const [toasts, setToasts] = useState<{id: number; message: string; kind: ToastKind}[]>([]);

  const toast = useCallback((message: string, kind: ToastKind = "success") => {
    const id = Date.now() + Math.random();
    setToasts((items) => [...items, {id, message, kind}]);
    window.setTimeout(() => {
      setToasts((items) => items.filter((item) => item.id !== id));
    }, 3600);
  }, []);

  const apiGet = useCallback(async <T,>(endpoint: string) => {
    if (!bridgeRef.current) throw new Error("AstrBot 插件页面桥接器尚未连接。");
    return unwrap<T>(await bridgeRef.current.apiGet(endpoint));
  }, []);

  const apiPost = useCallback(async <T,>(endpoint: string, body: unknown = {}) => {
    if (!bridgeRef.current) throw new Error("AstrBot 插件页面桥接器尚未连接。");
    return unwrap<T>(await bridgeRef.current.apiPost(endpoint, body));
  }, []);

  const selectedRows = useMemo(
    () => sortedSubscriptions(subscriptions.filter((item) => item.umo === selectedUmo)),
    [subscriptions, selectedUmo],
  );

  const copyOptions = useMemo(
    () => sortedSubscriptions(subscriptions.filter((item) => item.umo !== selectedUmo)),
    [subscriptions, selectedUmo],
  );

  const sessionMap = useMemo(() => new Map(sessions.map((item) => [item.umo, item])), [sessions]);
  const activeCount = subscriptions.filter((item) => item.enabled).length;
  const lowestSubscription = sortedSubscriptions(subscriptions).find((item) => item.latest_value != null);
  const selectedQuickTarget = selectedRows.find((item) => item.latest_value != null) || selectedRows[0];

  const loadData = useCallback(async () => {
    setRuntimeStatus("connecting");
    const data = await apiGet<BootstrapData>("bootstrap");
    setRevision(data.revision);
    setSessions(data.sessions);
    setSubscriptions(data.subscriptions);
    setDiagnostics(data.diagnostics);
    setCredentials(data.credentials);
    setAdminNoticeUmo(data.admin_notice_umo || "");
    setSelectedUmo((current) => {
      const valid = data.sessions.some((item) => item.umo === current);
      const currentHasSubscriptions = data.subscriptions.some((item) => item.umo === current);
      if (valid && currentHasSubscriptions) return current;
      return bestSessionUmo(data.sessions, data.subscriptions);
    });
    setHistorySubscriptionId((current) => {
      if (data.subscriptions.some((item) => String(item.id) === String(current))) return current;
      return data.subscriptions[0] ? String(data.subscriptions[0].id) : "";
    });
    setRuntimeStatus("ok");
  }, [apiGet]);

  const runBusy = useCallback(
    async (key: string, work: () => Promise<void>) => {
      setBusyAction(key);
      try {
        await work();
      } catch (error) {
        const typed = error as Error & {code?: number};
        toast(typed.message || String(error), "error");
        if (typed.code === 409) await loadData();
      } finally {
        setBusyAction("");
      }
    },
    [loadData, toast],
  );

  useEffect(() => {
    let cancelled = false;

    async function waitForBridge() {
      for (let attempt = 0; attempt < 50; attempt += 1) {
        if (window.AstrBotPluginPage?.ready) return window.AstrBotPluginPage;
        await new Promise((resolve) => window.setTimeout(resolve, 100));
      }
      throw new Error("AstrBot 插件页面桥接器加载超时。");
    }

    async function initialize() {
      try {
        const bridge = await waitForBridge();
        await bridge.ready();
        if (cancelled) return;
        bridgeRef.current = bridge;
        await loadData();
      } catch (error) {
        if (cancelled) return;
        setRuntimeStatus("error");
        toast((error as Error).message || String(error), "error");
      }
    }

    void initialize();
    return () => {
      cancelled = true;
    };
  }, [loadData, toast]);

  const updateEditor = (patch: Partial<EditorState>) => setEditor((current) => ({...current, ...patch}));

  const resetEditor = useCallback(() => {
    setEditor(emptyEditor);
    setEditingRoom(null);
    setAreas([]);
    setBuildings([]);
    setFloors([]);
    setRooms([]);
    setRoomHint("请点击“加载校区”并逐级选择寝室。");
    setQueryResult("尚未查询。");
  }, []);

  const editSubscription = useCallback((item: Subscription) => {
    setEditor({
      subscriptionId: String(item.id),
      alias: item.alias,
      threshold: String(item.threshold),
      unit: item.unit,
      intervalMinutes: String(item.interval_seconds / 60),
      enabled: item.enabled,
      manualEnabled: false,
      manualAreaId: item.room.area_id,
      manualBuildingCode: item.room.building_code,
      manualFloorCode: item.room.floor_code,
      manualRoomCode: item.room.room_code,
      manualRoomName: item.room.room_name || "",
    });
    setEditingRoom(item.room);
    setAreas([{code: item.room.area_id, name: item.room.area_name || item.room.area_id}]);
    setBuildings([
      {code: item.room.building_code, name: item.room.building_name || item.room.building_code},
    ]);
    setFloors([{code: item.room.floor_code, name: item.room.floor_name || item.room.floor_code}]);
    setRooms([{code: item.room.room_code, name: item.room.room_name || item.room.room_code}]);
    setRoomHint(roomTitle(item.room));
    setQueryResult("尚未查询。");

    void runBusy("locations:refresh", async () => {
      const areaData = await apiPost<{items: LocationItem[]}>("locations/areas");
      setAreas(withSelected(areaData.items, item.room.area_id, item.room.area_name || item.room.area_id));
      const buildingData = await apiPost<{items: LocationItem[]}>("locations/buildings", {
        area_id: item.room.area_id,
      });
      setBuildings(
        withSelected(
          buildingData.items,
          item.room.building_code,
          item.room.building_name || item.room.building_code,
        ),
      );
      const floorData = await apiPost<{items: LocationItem[]}>("locations/floors", {
        area_id: item.room.area_id,
        building_code: item.room.building_code,
      });
      setFloors(
        withSelected(floorData.items, item.room.floor_code, item.room.floor_name || item.room.floor_code),
      );
      const roomData = await apiPost<{items: LocationItem[]}>("locations/rooms", {
        area_id: item.room.area_id,
        building_code: item.room.building_code,
        floor_code: item.room.floor_code,
      });
      setRooms(
        withSelected(roomData.items, item.room.room_code, item.room.room_name || item.room.room_code),
      );
      setRoomHint("已加载当前位置的同级选项，可重新选择楼层或房间。");
    });
  }, [apiPost, runBusy]);

  const withSelected = (items: LocationItem[], value: string, label: string) => {
    if (!value || items.some((item) => String(item.code) === String(value))) return items;
    return [{code: value, name: label || value}, ...items];
  };

  const loadAreas = () =>
    runBusy("locations:areas", async () => {
      const data = await apiPost<{items: LocationItem[]}>("locations/areas");
      setAreas(data.items);
      setBuildings([]);
      setFloors([]);
      setRooms([]);
      setEditingRoom(null);
      setEditor((current) => ({
        ...current,
        manualAreaId: "",
        manualBuildingCode: "",
        manualFloorCode: "",
        manualRoomCode: "",
      }));
      setRoomHint(`已读取 ${data.items.length} 个校区。`);
    });

  const loadBuildings = (areaId: string) =>
    runBusy("locations:buildings", async () => {
      if (!areaId) return;
      const data = await apiPost<{items: LocationItem[]}>("locations/buildings", {area_id: areaId});
      setBuildings(data.items);
      setFloors([]);
      setRooms([]);
      setEditingRoom(null);
      updateEditor({manualAreaId: areaId, manualBuildingCode: "", manualFloorCode: "", manualRoomCode: ""});
      setRoomHint(data.items.length ? `已读取 ${data.items.length} 栋楼。` : "该校区未返回楼栋。");
    });

  const loadFloors = (buildingCode: string) =>
    runBusy("locations:floors", async () => {
      if (!editor.manualAreaId || !buildingCode) return;
      const data = await apiPost<{items: LocationItem[]}>("locations/floors", {
        area_id: editor.manualAreaId,
        building_code: buildingCode,
      });
      setFloors(data.items);
      setRooms([]);
      setEditingRoom(null);
      updateEditor({manualBuildingCode: buildingCode, manualFloorCode: "", manualRoomCode: ""});
      setRoomHint(data.items.length ? `已读取 ${data.items.length} 个楼层。` : "该楼栋未返回楼层。");
    });

  const loadRooms = (floorCode: string) =>
    runBusy("locations:rooms", async () => {
      if (!editor.manualAreaId || !editor.manualBuildingCode || !floorCode) return;
      const data = await apiPost<{items: LocationItem[]}>("locations/rooms", {
        area_id: editor.manualAreaId,
        building_code: editor.manualBuildingCode,
        floor_code: floorCode,
      });
      setRooms(data.items);
      setEditingRoom(null);
      updateEditor({manualFloorCode: floorCode, manualRoomCode: ""});
      setRoomHint(data.items.length ? `已读取 ${data.items.length} 个房间。` : "该楼层未返回房间。");
    });

  const currentRoom = (): RoomRef => {
    if (editor.manualEnabled) {
      const values = {
        area_id: editor.manualAreaId.trim(),
        building_code: editor.manualBuildingCode.trim(),
        floor_code: editor.manualFloorCode.trim(),
        room_code: editor.manualRoomCode.trim(),
      };
      const missing = Object.entries(values)
        .filter(([, value]) => !value)
        .map(([key]) => key);
      if (missing.length) throw new Error(`手动房间参数不完整：${missing.join("、")}。`);
      return {
        ...values,
        area_name: "",
        building_name: "",
        floor_name: "",
        room_name: editor.manualRoomName.trim() || editor.alias.trim() || values.room_code,
      };
    }
    if (!editor.manualAreaId || !editor.manualBuildingCode || !editor.manualFloorCode || !editor.manualRoomCode) {
      if (editingRoom) return editingRoom;
      throw new Error("请完整选择校区、楼栋、楼层和房间。");
    }
    return {
      area_id: editor.manualAreaId,
      area_name: areas.find((item) => String(item.code) === editor.manualAreaId)?.name || "",
      building_code: editor.manualBuildingCode,
      building_name: buildings.find((item) => String(item.code) === editor.manualBuildingCode)?.name || "",
      floor_code: editor.manualFloorCode,
      floor_name: floors.find((item) => String(item.code) === editor.manualFloorCode)?.name || "",
      room_code: editor.manualRoomCode,
      room_name: rooms.find((item) => String(item.code) === editor.manualRoomCode)?.name || "",
    };
  };

  const readConfig = () => {
    const alias = editor.alias.trim();
    const threshold = editor.threshold.trim();
    const unit = editor.unit.trim();
    const interval = Number(editor.intervalMinutes);
    if (!alias) throw new Error("请填写订阅别名。");
    if (!threshold || !Number.isFinite(Number(threshold))) throw new Error("阈值不是有效数字。");
    if (!unit) throw new Error("请填写单位。");
    if (!Number.isInteger(interval) || interval < 5 || interval > 1440) {
      throw new Error("查询频率必须是 5-1440 分钟的整数。");
    }
    return {
      alias,
      threshold,
      unit,
      interval_seconds: interval * 60,
      enabled: editor.enabled,
    };
  };

  const saveSubscription = () =>
    runBusy("subscription:save", async () => {
      if (!selectedUmo) throw new Error("请先选择会话。");
      const result = await apiPost<{
        message: string;
        subscription: Subscription;
        report?: QueryReport;
      }>("subscriptions/save", {
        revision,
        umo: selectedUmo,
        subscription_id: editor.subscriptionId || null,
        room: currentRoom(),
        config: readConfig(),
      });
      const item = result.report?.items?.[0];
      const errors = result.report?.errors || [];
      setQueryResult(
        item
          ? `${item.alias}：${item.value} ${item.unit}${item.balance ? `\n余额：${item.balance}` : ""}\n${item.room_name || ""}\n${formatTime(item.captured_at)}`
          : errors.join("；") || "订阅已保存，首次查询未取得数据。",
      );
      toast(result.message, errors.length ? "error" : "success");
      await loadData();
      editSubscription(result.subscription);
    });

  const deleteSubscription = () =>
    runBusy("subscription:delete", async () => {
      const result = await apiPost<{message: string}>("subscriptions/delete", {
        revision,
        umo: selectedUmo,
        subscription_id: editor.subscriptionId,
      });
      toast(result.message);
      resetEditor();
      await loadData();
    });

  const querySubscription = () =>
    runBusy("subscription:query", async () => {
      const result = await apiPost<{message: string; report: QueryReport}>("query/run", {
        subscription_id: editor.subscriptionId,
      });
      const item = result.report.items?.[0];
      setQueryResult(
        item
          ? `${item.alias}：${item.value} ${item.unit}${item.balance ? `\n余额：${item.balance}` : ""}\n${item.room_name || ""}\n${formatTime(item.captured_at)}`
          : result.report.errors?.join("；") || "没有取得数据。",
      );
      toast(result.message);
      await loadData();
    });

  const copySubscription = () =>
    runBusy("subscription:copy", async () => {
      if (!selectedUmo) throw new Error("请先选择目标会话。");
      const source = subscriptions.find((item) => String(item.id) === String(copySubscriptionId));
      if (!source) throw new Error("请选择要复制的订阅。");
      const result = await apiPost<{
        message: string;
        subscription: Subscription;
        report?: QueryReport;
      }>("subscriptions/save", {
        revision,
        umo: selectedUmo,
        subscription_id: null,
        room: source.room,
        config: {
          alias: uniqueAlias(source.alias, selectedRows),
          threshold: source.threshold,
          unit: source.unit,
          interval_seconds: source.interval_seconds,
          enabled: source.enabled,
        },
      });
      const errors = result.report?.errors || [];
      toast(result.message, errors.length ? "error" : "success");
      await loadData();
      editSubscription(result.subscription);
    });

  const loadHistory = useCallback(
    (subscriptionId: string) =>
      runBusy("history:load", async () => {
        if (!subscriptionId) throw new Error("暂无可查看的寝室订阅。");
        const result = await apiPost<{
          subscription: Subscription;
          items: Reading[];
        }>("history", {subscription_id: subscriptionId});
        setHistoryItems(result.items);
        setHistoryUnit(result.subscription.unit);
        const values = result.items.map((item) => Number(item.value));
        setHistorySummary(
          values.length
            ? `共 ${values.length} 个采样，最低 ${Math.min(...values)} ${result.subscription.unit}，最高 ${Math.max(...values)} ${result.subscription.unit}。`
            : "最近 30 天暂无采样。",
        );
      }),
    [apiPost, runBusy],
  );

  useEffect(() => {
    if (!selectedQuickTarget) return;
    void loadHistory(String(selectedQuickTarget.id));
  }, [selectedQuickTarget?.id]);

  const currentHistorySubscription = subscriptions.find(
    (item) => String(item.id) === String(historySubscriptionId),
  );

  const chartData = historyItems.map((item) => ({
    time: new Date(item.captured_at * 1000).toLocaleDateString(),
    value: Number(item.value),
  }));

  const runtimeChip = runtimeStatus === "ok" ? (
    <Chip color="success" variant="soft">
      <CircleCheck className="size-3" />
      已连接
    </Chip>
  ) : runtimeStatus === "error" ? (
    <Chip color="danger" variant="soft">
      <CircleExclamation className="size-3" />
      连接失败
    </Chip>
  ) : (
    <Chip color="warning" variant="soft">
      <Spinner size="sm" />
      正在连接
    </Chip>
  );

  return (
    <main className="plugin-shell">
      <div className="page-width flex flex-col gap-6 px-6 py-8">
        <header className="flex flex-col gap-4 lg:flex-row lg:items-start lg:justify-between">
          <div className="flex flex-col gap-2">
            <p className="text-muted text-xs font-semibold uppercase tracking-[0.18em]">AstrBot 插件后台</p>
            <div className="flex flex-wrap items-center gap-3">
              <h1 className="text-foreground text-2xl font-semibold tracking-tight">易校园电费监控</h1>
              <Chip variant="soft">v1.4.0</Chip>
            </div>
            <p className="text-muted max-w-2xl text-sm">
              多会话、多寝室查询，低电量跨阈值提醒。当前页面使用 HeroUI / HeroUI Pro 重构。
            </p>
          </div>
          <div className="flex flex-wrap items-center gap-2">
            {runtimeChip}
            <Button
              size="sm"
              variant="secondary"
              isPending={busyAction === "refresh"}
              onPress={() => runBusy("refresh", loadData)}
            >
              <Magnifier className="size-4" />
              刷新
            </Button>
          </div>
        </header>

        <section className="grid grid-cols-1 gap-3 sm:grid-cols-2 xl:grid-cols-4" aria-label="运行概览">
          <KPI>
            <KPI.Header>
              <KPI.Icon status="success">
                <Persons />
              </KPI.Icon>
              <KPI.Title>近期会话</KPI.Title>
            </KPI.Header>
            <KPI.Content>
              <KPI.Value value={sessions.length} />
            </KPI.Content>
            <KPI.Footer>
              <span className="text-muted text-xs">当前会话数量</span>
            </KPI.Footer>
          </KPI>
          <KPI>
            <KPI.Header>
              <KPI.Icon status="success">
                <House />
              </KPI.Icon>
              <KPI.Title>寝室订阅</KPI.Title>
            </KPI.Header>
            <KPI.Content>
              <KPI.Value value={subscriptions.length} />
            </KPI.Content>
            <KPI.Footer>
              <span className="text-muted text-xs">已订阅寝室数量</span>
            </KPI.Footer>
          </KPI>
          <KPI>
            <KPI.Header>
              <KPI.Icon status={activeCount ? "success" : "warning"}>
                <ShieldCheck />
              </KPI.Icon>
              <KPI.Title>监控中</KPI.Title>
            </KPI.Header>
            <KPI.Content>
              <KPI.Value value={activeCount} />
            </KPI.Content>
            <KPI.Footer>
              <span className="text-muted text-xs">正在监控的寝室</span>
            </KPI.Footer>
          </KPI>
          <KPI>
            <KPI.Header>
              <KPI.Icon status={diagnostics.length ? "warning" : "success"}>
                <Database />
              </KPI.Icon>
              <KPI.Title>诊断记录</KPI.Title>
            </KPI.Header>
            <KPI.Content>
              <KPI.Value value={diagnostics.length} />
            </KPI.Content>
            <KPI.Footer>
              <span className="text-muted text-xs">历史诊断记录数</span>
            </KPI.Footer>
          </KPI>
        </section>

        {lowestSubscription && Number(lowestSubscription.latest_value) <= Number(lowestSubscription.threshold) ? (
          <Alert status="warning">
            <Alert.Indicator />
            <Alert.Content>
              <Alert.Title>存在低电量寝室</Alert.Title>
              <Alert.Description>
                {lowestSubscription.alias} 当前 {lowestSubscription.latest_value} {lowestSubscription.unit}，
                阈值 {lowestSubscription.threshold} {lowestSubscription.unit}。
              </Alert.Description>
            </Alert.Content>
          </Alert>
        ) : null}

        <Tabs selectedKey={tab} onSelectionChange={(key) => setTab(String(key) as TabKey)}>
          <Tabs.ListContainer>
            <Tabs.List
              aria-label="电费监控管理页面"
              className="w-fit *:min-w-20 *:whitespace-nowrap *:break-keep *:text-center [&_.tabs__tab]:whitespace-nowrap [&_.tabs__tab]:break-keep"
            >
              <Tabs.Tab id="subscriptions">订阅<Tabs.Indicator /></Tabs.Tab>
              <Tabs.Tab id="credentials" className="min-w-28">登录与通知<Tabs.Indicator /></Tabs.Tab>
              <Tabs.Tab id="history">趋势<Tabs.Indicator /></Tabs.Tab>
              <Tabs.Tab id="diagnostics">诊断<Tabs.Indicator /></Tabs.Tab>
            </Tabs.List>
          </Tabs.ListContainer>

          <Tabs.Panel id="subscriptions" className="pt-5">
            <SubscriptionsPanel
              sessions={sessions}
              subscriptions={subscriptions}
              selectedRows={selectedRows}
              selectedUmo={selectedUmo}
              setSelectedUmo={(value) => {
                setSelectedUmo(value);
                resetEditor();
              }}
              copyOptions={copyOptions}
              copySubscriptionId={copySubscriptionId}
              setCopySubscriptionId={setCopySubscriptionId}
              sessionMap={sessionMap}
              editor={editor}
              editingRoom={editingRoom}
              updateEditor={updateEditor}
              resetEditor={resetEditor}
              editSubscription={editSubscription}
              queryResult={queryResult}
              areas={areas}
              buildings={buildings}
              floors={floors}
              rooms={rooms}
              roomHint={roomHint}
              busyAction={busyAction}
              loadAreas={loadAreas}
              loadBuildings={loadBuildings}
              loadFloors={loadFloors}
              loadRooms={loadRooms}
              saveSubscription={saveSubscription}
              deleteSubscription={deleteSubscription}
              querySubscription={querySubscription}
              copySubscription={copySubscription}
              importSessions={() =>
                runBusy("sessions:import", async () => {
                  const result = await apiPost<{message: string}>("sessions/import");
                  toast(result.message);
                  await loadData();
                })
              }
              quickTarget={selectedQuickTarget}
              chartData={chartData}
              historyUnit={historyUnit}
              historySummary={historySummary}
              openHistory={() => {
                if (selectedQuickTarget) setHistorySubscriptionId(String(selectedQuickTarget.id));
                setTab("history");
              }}
            />
          </Tabs.Panel>

          <Tabs.Panel id="credentials" className="pt-5">
            <CredentialsPanel
              credentials={credentials}
              sessions={sessions}
              adminNoticeUmo={adminNoticeUmo}
              setAdminNoticeUmo={setAdminNoticeUmo}
              shiroJID={shiroJID}
              setShiroJID={setShiroJID}
              ymId={ymId}
              setYmId={setYmId}
              busyAction={busyAction}
              saveCredentials={() =>
                runBusy("credentials:save", async () => {
                  const result = await apiPost<{message: string}>("credentials/save", {shiroJID, ymId});
                  setShiroJID("");
                  setYmId("");
                  toast(result.message);
                  await loadData();
                })
              }
              verifyCredentials={() =>
                runBusy("credentials:verify", async () => {
                  const result = await apiPost<{message: string}>("credentials/verify");
                  toast(result.message);
                  await loadData();
                })
              }
              clearCredentials={() =>
                runBusy("credentials:clear", async () => {
                  const result = await apiPost<{message: string}>("credentials/clear");
                  toast(result.message);
                  await loadData();
                })
              }
              saveAdminNotice={() =>
                runBusy("admin:save", async () => {
                  const result = await apiPost<{message: string}>("settings/admin-notice", {
                    umo: adminNoticeUmo,
                  });
                  toast(result.message);
                  await loadData();
                })
              }
              testNotification={() =>
                runBusy("admin:test", async () => {
                  const result = await apiPost<{message: string}>("notification/test");
                  toast(result.message);
                })
              }
            />
          </Tabs.Panel>

          <Tabs.Panel id="history" className="pt-5">
            <HistoryPanel
              subscriptions={subscriptions}
              sessions={sessions}
              historySubscriptionId={historySubscriptionId}
              setHistorySubscriptionId={setHistorySubscriptionId}
              currentHistorySubscription={currentHistorySubscription}
              chartData={chartData}
              historyUnit={historyUnit}
              historySummary={historySummary}
              busyAction={busyAction}
              loadHistory={() => loadHistory(historySubscriptionId)}
            />
          </Tabs.Panel>

          <Tabs.Panel id="diagnostics" className="pt-5">
            <DiagnosticsPanel diagnostics={diagnostics} />
          </Tabs.Panel>
        </Tabs>
      </div>

      <div className="fixed bottom-6 right-6 z-20 flex w-[min(24rem,calc(100vw-3rem))] flex-col gap-2">
        {toasts.map((item) => (
          <Alert key={item.id} status={item.kind === "error" ? "danger" : "success"}>
            <Alert.Indicator />
            <Alert.Content>
              <Alert.Title>{item.message}</Alert.Title>
            </Alert.Content>
          </Alert>
        ))}
      </div>
    </main>
  );
}

function SubscriptionsPanel(props: {
  sessions: SessionRecord[];
  subscriptions: Subscription[];
  selectedRows: Subscription[];
  selectedUmo: string;
  setSelectedUmo: (value: string) => void;
  copyOptions: Subscription[];
  copySubscriptionId: string;
  setCopySubscriptionId: (value: string) => void;
  sessionMap: Map<string, SessionRecord>;
  editor: EditorState;
  editingRoom: RoomRef | null;
  updateEditor: (patch: Partial<EditorState>) => void;
  resetEditor: () => void;
  editSubscription: (item: Subscription) => void;
  queryResult: string;
  areas: LocationItem[];
  buildings: LocationItem[];
  floors: LocationItem[];
  rooms: LocationItem[];
  roomHint: string;
  busyAction: string;
  loadAreas: () => void;
  loadBuildings: (areaId: string) => void;
  loadFloors: (buildingCode: string) => void;
  loadRooms: (floorCode: string) => void;
  saveSubscription: () => void;
  deleteSubscription: () => void;
  querySubscription: () => void;
  copySubscription: () => void;
  importSessions: () => void;
  quickTarget?: Subscription;
  chartData: {time: string; value: number}[];
  historyUnit: string;
  historySummary: string;
  openHistory: () => void;
}) {
  const {
    sessions,
    selectedRows,
    selectedUmo,
    setSelectedUmo,
    copyOptions,
    copySubscriptionId,
    setCopySubscriptionId,
    sessionMap,
    editor,
    updateEditor,
    resetEditor,
    editSubscription,
    queryResult,
    areas,
    buildings,
    floors,
    rooms,
    roomHint,
    busyAction,
    loadAreas,
    loadBuildings,
    loadFloors,
    loadRooms,
    saveSubscription,
    deleteSubscription,
    querySubscription,
    copySubscription,
    importSessions,
    quickTarget,
    chartData,
    historyUnit,
    historySummary,
    openHistory,
  } = props;

  return (
    <div className="flex flex-col gap-4">
      <Card className="rounded-2xl">
        <Card.Header className="flex-col items-start gap-1">
          <Card.Title>会话与订阅</Card.Title>
          <Card.Description>选择会话、复制已有订阅，或创建新的寝室监控。</Card.Description>
        </Card.Header>
        <Card.Content className="grid gap-4 lg:grid-cols-[minmax(240px,1fr)_minmax(280px,1.2fr)_auto] lg:items-end">
          <SelectField label="会话" value={selectedUmo} onChange={setSelectedUmo}>
            <NativeSelect.Option value="">请选择会话</NativeSelect.Option>
            {sessions.map((session) => (
              <NativeSelect.Option key={session.umo} value={session.umo}>
                {sessionLabel(session)}
              </NativeSelect.Option>
            ))}
            <NativeSelect.Indicator />
          </SelectField>
          <SelectField label="复制已有订阅" value={copySubscriptionId} onChange={setCopySubscriptionId}>
            <NativeSelect.Option value="">
              {copyOptions.length ? "选择要复制的订阅" : "暂无可复制订阅"}
            </NativeSelect.Option>
            {copyOptions.map((item) => (
              <NativeSelect.Option key={item.id} value={String(item.id)}>
                {item.alias} · {sessionLabel(sessionMap.get(item.umo))}
              </NativeSelect.Option>
            ))}
            <NativeSelect.Indicator />
          </SelectField>
          <div className="flex flex-wrap items-center justify-end gap-2">
            <Button
              size="sm"
              variant="secondary"
              isDisabled={!copyOptions.length || !copySubscriptionId}
              isPending={busyAction === "subscription:copy"}
              onPress={copySubscription}
            >
              <Copy className="size-4" />
              复制
            </Button>
            <Button size="sm" variant="secondary" isPending={busyAction === "sessions:import"} onPress={importSessions}>
              <Database className="size-4" />
              导入
            </Button>
            <Button
              size="sm"
              onPress={() => {
                resetEditor();
                window.setTimeout(() => document.getElementById("subscription-editor")?.scrollIntoView({behavior: "smooth"}), 0);
              }}
            >
              <Plus className="size-4" />
              新增寝室
            </Button>
          </div>
        </Card.Content>
        <Card.Footer>
          <p className="text-muted text-xs">
            私聊昵称来自机器人收到消息时的平台昵称；历史导入拿不到昵称时只显示 QQ 号。
          </p>
        </Card.Footer>
      </Card>

      <div className="grid gap-4 xl:grid-cols-[minmax(0,1.35fr)_minmax(420px,0.85fr)]">
        <Card className="rounded-2xl">
          <Card.Header className="flex-row items-start justify-between">
            <div>
              <Card.Title>当前会话订阅</Card.Title>
              <Card.Description>低电量优先展示，便于快速处理风险寝室。</Card.Description>
            </div>
            <Chip variant="soft">{selectedRows.length}</Chip>
          </Card.Header>
          <Card.Content className="flex flex-col gap-4">
            <Table variant="secondary" className="compact-table">
              <Table.ScrollContainer>
                <Table.Content
                  aria-label="当前会话订阅"
                  className="min-w-[880px] [&_th]:whitespace-nowrap [&_th]:break-keep"
                >
                  <Table.Header>
                    <Table.Column id="alias" isRowHeader>别名</Table.Column>
                    <Table.Column id="latest">最新电量</Table.Column>
                    <Table.Column id="threshold">阈值 / 频率</Table.Column>
                    <Table.Column id="status">状态</Table.Column>
                    <Table.Column id="action">操作</Table.Column>
                  </Table.Header>
                  <Table.Body
                    items={selectedRows}
                    renderEmptyState={() => (
                      <div className="text-muted flex h-24 items-center justify-center text-sm">
                        当前会话尚未配置寝室。
                      </div>
                    )}
                  >
                    {(item) => (
                      <Table.Row id={item.id}>
                        <Table.Cell>
                          <div className="flex flex-col gap-1">
                            <span className="font-medium">{item.alias}</span>
                            <span className="text-muted mono-wrap text-xs">{roomTitle(item.room)}</span>
                          </div>
                        </Table.Cell>
                        <Table.Cell>
                          <div className="flex flex-col gap-1">
                            <span className="font-medium whitespace-nowrap">
                              {item.latest_value == null
                                ? item.last_error
                                  ? "查询失败"
                                  : "等待首次查询"
                                : `${item.latest_value} ${item.unit}`}
                            </span>
                            <span className="text-muted text-xs">
                              {item.latest_balance ? `余额 ${item.latest_balance} · ` : ""}
                              {formatTime(item.latest_at)}
                            </span>
                            {item.last_error ? <span className="text-danger text-xs">{item.last_error}</span> : null}
                          </div>
                        </Table.Cell>
                        <Table.Cell>
                          <div className="flex flex-col gap-1">
                            <span className="whitespace-nowrap">{item.threshold} {item.unit}</span>
                            <span className="text-muted whitespace-nowrap text-xs">{item.interval_seconds / 60} 分钟</span>
                          </div>
                        </Table.Cell>
                        <Table.Cell>
                          <div className="flex flex-col items-start gap-1 whitespace-nowrap">
                            <StatusChip subscription={item} />
                            {item.alerted ? <span className="text-warning text-xs">已触发低电量提醒</span> : null}
                          </div>
                        </Table.Cell>
                        <Table.Cell>
                          <Button className="whitespace-nowrap" size="sm" variant="tertiary" onPress={() => editSubscription(item)}>
                            <Gear className="size-4" />
                            编辑
                          </Button>
                        </Table.Cell>
                      </Table.Row>
                    )}
                  </Table.Body>
                </Table.Content>
              </Table.ScrollContainer>
            </Table>

            <section className="rounded-2xl bg-surface-secondary p-4">
              <div className="mb-4 flex items-start justify-between gap-3">
                <div>
                  <h3 className="text-foreground text-base font-semibold">
                    {quickTarget ? `近30天电量趋势（${quickTarget.alias}）` : "近30天电量趋势"}
                  </h3>
                  <p className="text-muted text-sm">优先展示当前会话电量最低的寝室。</p>
                </div>
                <Button size="sm" variant="secondary" onPress={openHistory}>
                  <ChartLine className="size-4" />
                  更多趋势
                </Button>
              </div>
              <div className="flex flex-col gap-3">
                {chartData.length ? (
                  <LineChart data={chartData} height={220}>
                    <LineChart.Grid vertical={false} />
                    <LineChart.XAxis dataKey="time" tickMargin={8} />
                    <LineChart.YAxis width={48} />
                    <LineChart.Line dataKey="value" dot={false} name={`电量（${historyUnit}）`} stroke="var(--color-accent)" strokeWidth={2} type="monotone" />
                    <LineChart.Tooltip content={<LineChart.TooltipContent />} />
                  </LineChart>
                ) : (
                  <div className="text-muted flex h-[220px] items-center justify-center text-sm">暂无历史采样</div>
                )}
                <p className="text-muted text-xs">{historySummary}</p>
              </div>
            </section>
          </Card.Content>
        </Card>

        <Card id="subscription-editor" className="rounded-2xl">
          <Card.Header className="flex-col items-start gap-1">
            <Card.Title>{editor.subscriptionId ? `编辑：${editor.alias}` : "新增寝室"}</Card.Title>
            <Card.Description>填写基础参数，然后逐级选择寝室。</Card.Description>
          </Card.Header>
          <Card.Content>
            <Form className="flex flex-col gap-4" onSubmit={(event) => event.preventDefault()}>
              <TextInput label="别名" value={editor.alias} onChange={(alias) => updateEditor({alias})} placeholder="例如：厚德苑6号240" maxLength={40} />
              <div className="grid gap-4 md:grid-cols-2">
                <TextInput label="低电量阈值" type="number" value={editor.threshold} onChange={(threshold) => updateEditor({threshold})} />
                <TextInput label="单位" value={editor.unit} onChange={(unit) => updateEditor({unit})} maxLength={12} />
              </div>
              <div className="grid gap-4 md:grid-cols-2">
                <TextInput label="查询频率（分钟）" type="number" value={editor.intervalMinutes} onChange={(intervalMinutes) => updateEditor({intervalMinutes})} />
                <Switch isSelected={editor.enabled} onChange={(enabled) => updateEditor({enabled})}>
                  <Switch.Content className="text-sm">
                    <Switch.Control>
                      <Switch.Thumb />
                    </Switch.Control>
                    启用后台监控
                  </Switch.Content>
                </Switch>
              </div>

              <div className="flex flex-col gap-3 border-t border-separator pt-4">
                <div>
                  <h3 className="text-sm font-semibold">逐级选择寝室</h3>
                  <p className="text-muted text-xs">{roomHint}</p>
                </div>
                <div className="grid gap-4 md:grid-cols-2">
                  <SelectField
                    label="校区"
                    value={editor.manualAreaId}
                    disabled={editor.manualEnabled}
                    onChange={(value) => {
                      updateEditor({manualAreaId: value});
                      loadBuildings(value);
                    }}
                  >
                    <OptionList items={areas} placeholder="请选择校区" />
                  </SelectField>
                  <SelectField
                    label="楼栋"
                    value={editor.manualBuildingCode}
                    disabled={editor.manualEnabled}
                    onChange={(value) => {
                      updateEditor({manualBuildingCode: value});
                      loadFloors(value);
                    }}
                  >
                    <OptionList items={buildings} placeholder="请选择楼栋" />
                  </SelectField>
                </div>
                <div className="grid gap-4 md:grid-cols-2">
                  <SelectField
                    label="楼层"
                    value={editor.manualFloorCode}
                    disabled={editor.manualEnabled}
                    onChange={(value) => {
                      updateEditor({manualFloorCode: value});
                      loadRooms(value);
                    }}
                  >
                    <OptionList items={floors} placeholder="请选择楼层" />
                  </SelectField>
                  <SelectField
                    label="房间"
                    value={editor.manualRoomCode}
                    disabled={editor.manualEnabled}
                    onChange={(value) => {
                      updateEditor({manualRoomCode: value});
                      const roomName = rooms.find((item) => String(item.code) === value)?.name || value;
                      props.updateEditor({manualRoomName: roomName});
                    }}
                  >
                    <OptionList items={rooms} placeholder="请选择房间" />
                  </SelectField>
                </div>
                <Button size="sm" variant="secondary" isPending={busyAction === "locations:areas"} onPress={loadAreas}>
                  <Magnifier className="size-4" />
                  加载校区
                </Button>
              </div>

              <section className="flex flex-col gap-4 rounded-2xl bg-surface-secondary p-4">
                <div>
                  <h3 className="text-foreground text-base font-semibold">高级设置 / 手动接口参数</h3>
                  <p className="text-muted text-sm">从 queryRoomSurplus 请求中取得四个参数。platform 固定为 YUNMA_APP。</p>
                </div>
                  <Switch
                    isSelected={editor.manualEnabled}
                    onChange={(manualEnabled) => updateEditor({manualEnabled})}
                  >
                    <Switch.Content className="text-sm">
                      <Switch.Control>
                        <Switch.Thumb />
                      </Switch.Control>
                      使用手动参数，不经过逐级选择
                    </Switch.Content>
                  </Switch>
                  <div className="grid gap-4 md:grid-cols-2">
                    <TextInput label="areaId" value={editor.manualAreaId} disabled={!editor.manualEnabled} onChange={(manualAreaId) => updateEditor({manualAreaId})} placeholder="例如：2510120172541411338" />
                    <TextInput label="buildingCode" value={editor.manualBuildingCode} disabled={!editor.manualEnabled} onChange={(manualBuildingCode) => updateEditor({manualBuildingCode})} placeholder="例如：39" />
                  </div>
                  <div className="grid gap-4 md:grid-cols-2">
                    <TextInput label="floorCode" value={editor.manualFloorCode} disabled={!editor.manualEnabled} onChange={(manualFloorCode) => updateEditor({manualFloorCode})} placeholder="例如：71" />
                    <TextInput label="roomCode" value={editor.manualRoomCode} disabled={!editor.manualEnabled} onChange={(manualRoomCode) => updateEditor({manualRoomCode})} placeholder="例如：12598" />
                  </div>
                  <TextInput label="房间显示名称（可选）" value={editor.manualRoomName} disabled={!editor.manualEnabled} onChange={(manualRoomName) => updateEditor({manualRoomName})} placeholder="例如：厚德苑6号240" maxLength={80} />
              </section>

              <div className="flex flex-wrap gap-2">
                <Button isPending={busyAction === "subscription:save"} onPress={saveSubscription}>
                  <FloppyDisk className="size-4" />
                  保存订阅
                </Button>
                <Button
                  variant="danger-soft"
                  isDisabled={!editor.subscriptionId}
                  isPending={busyAction === "subscription:delete"}
                  onPress={deleteSubscription}
                >
                  <TrashBin className="size-4" />
                  删除
                </Button>
                <Button
                  variant="secondary"
                  isDisabled={!editor.subscriptionId}
                  isPending={busyAction === "subscription:query"}
                  onPress={querySubscription}
                >
                  <Play className="size-4" />
                  立即查询
                </Button>
              </div>
              <pre className="text-muted bg-surface-secondary mono-wrap whitespace-pre-wrap rounded-xl p-3 text-xs">
                {queryResult}
              </pre>
            </Form>
          </Card.Content>
        </Card>
      </div>
    </div>
  );
}

function CredentialsPanel(props: {
  credentials: CredentialState;
  sessions: SessionRecord[];
  adminNoticeUmo: string;
  setAdminNoticeUmo: (value: string) => void;
  shiroJID: string;
  setShiroJID: (value: string) => void;
  ymId: string;
  setYmId: (value: string) => void;
  busyAction: string;
  saveCredentials: () => void;
  verifyCredentials: () => void;
  clearCredentials: () => void;
  saveAdminNotice: () => void;
  testNotification: () => void;
}) {
  const credentialColor = props.credentials.state === "valid"
    ? "success"
    : props.credentials.state === "expired"
      ? "danger"
      : props.credentials.configured
        ? "warning"
        : "default";
  const privateSessions = props.sessions.filter((item) => item.chat_type === "private");

  return (
    <div className="grid gap-4 lg:grid-cols-2">
      <Card className="rounded-2xl">
        <Card.Header>
          <Card.Title>易校园登录态</Card.Title>
          <Card.Description>凭据仅保存到插件数据目录，不会在页面回显。</Card.Description>
        </Card.Header>
        <Card.Content>
          <Form className="flex flex-col gap-4" onSubmit={(event) => event.preventDefault()}>
            <Alert status={credentialColor === "danger" ? "danger" : credentialColor === "success" ? "success" : "warning"}>
              <Alert.Indicator />
              <Alert.Content>
                <Alert.Title>状态：{credentialLabel(props.credentials)}</Alert.Title>
                <Alert.Description>
                  {props.credentials.configured
                    ? `shiroJID ${props.credentials.shiro_jid_masked || "已保存"}，${
                        props.credentials.ym_id_configured
                          ? `ymId ${props.credentials.ym_id_masked || "已保存"}`
                          : "ymId 未填写（可选）"
                      }`
                    : "未保存凭据"}
                  {props.credentials.error ? `。${props.credentials.error}` : ""}
                </Alert.Description>
              </Alert.Content>
            </Alert>
            <TextInput label="shiroJID" type="password" value={props.shiroJID} onChange={props.setShiroJID} placeholder="可粘贴纯值或完整 Cookie" />
            <TextInput label="ymId（可选）" type="password" value={props.ymId} onChange={props.setYmId} placeholder="新版接口未携带时可留空" />
            <div className="flex flex-wrap gap-2">
              <Button isPending={props.busyAction === "credentials:save"} onPress={props.saveCredentials}>
                <Key className="size-4" />
                保存并验证
              </Button>
              <Button variant="secondary" isPending={props.busyAction === "credentials:verify"} onPress={props.verifyCredentials}>
                <Check className="size-4" />
                验证现有登录态
              </Button>
              <Button variant="danger-soft" isPending={props.busyAction === "credentials:clear"} onPress={props.clearCredentials}>
                清除
              </Button>
            </div>
            <Description>
              成功查询后会自动接收并保存服务端滚动更新的 shiroJID；登录失败响应不会覆盖凭据。
            </Description>
          </Form>
        </Card.Content>
      </Card>

      <Card className="rounded-2xl">
        <Card.Header>
          <Card.Title>登录过期通知</Card.Title>
          <Card.Description>选择一个已接触过机器人的私聊作为通知目标。</Card.Description>
        </Card.Header>
        <Card.Content className="flex flex-col gap-4">
          <SelectField label="管理员私聊目标" value={props.adminNoticeUmo} onChange={props.setAdminNoticeUmo}>
            <NativeSelect.Option value="">不发送私信通知</NativeSelect.Option>
            {privateSessions.map((session) => (
              <NativeSelect.Option key={session.umo} value={session.umo}>
                {sessionLabel(session)}
              </NativeSelect.Option>
            ))}
            <NativeSelect.Indicator />
          </SelectField>
          <div className="flex flex-wrap gap-2">
            <Button isPending={props.busyAction === "admin:save"} onPress={props.saveAdminNotice}>
              <FloppyDisk className="size-4" />
              保存目标
            </Button>
            <Button variant="secondary" isPending={props.busyAction === "admin:test"} onPress={props.testNotification}>
              <Bell className="size-4" />
              发送测试通知
            </Button>
          </div>
          <p className="text-muted text-sm">登录态过期后仅通知一次，更新凭据后重新布防。</p>
        </Card.Content>
      </Card>
    </div>
  );
}

function HistoryPanel(props: {
  subscriptions: Subscription[];
  sessions: SessionRecord[];
  historySubscriptionId: string;
  setHistorySubscriptionId: (value: string) => void;
  currentHistorySubscription?: Subscription;
  chartData: {time: string; value: number}[];
  historyUnit: string;
  historySummary: string;
  busyAction: string;
  loadHistory: () => void;
}) {
  const sessionMap = new Map(props.sessions.map((item) => [item.umo, item]));
  return (
    <Card className="rounded-2xl">
      <Card.Header className="flex-col items-start gap-1 lg:flex-row lg:items-end lg:justify-between">
        <div>
          <Card.Title>近30天用电趋势</Card.Title>
          <Card.Description>按采样时间展示最近 30 天剩余电量变化。</Card.Description>
        </div>
        <div className="flex w-full flex-col gap-2 lg:w-[460px] lg:flex-row lg:items-end">
          <SelectField label="寝室订阅" value={props.historySubscriptionId} onChange={props.setHistorySubscriptionId}>
            <NativeSelect.Option value="">请选择订阅</NativeSelect.Option>
            {props.subscriptions.map((item) => (
              <NativeSelect.Option key={item.id} value={String(item.id)}>
                {item.alias} · {sessionLabel(sessionMap.get(item.umo))}
              </NativeSelect.Option>
            ))}
            <NativeSelect.Indicator />
          </SelectField>
          <Button
            className="lg:mb-0.5"
            variant="secondary"
            isPending={props.busyAction === "history:load"}
            onPress={props.loadHistory}
          >
            <ChartLine className="size-4" />
            加载趋势
          </Button>
        </div>
      </Card.Header>
      <Card.Content className="flex flex-col gap-4">
        <div className="flex flex-wrap items-center gap-2">
          <Chip variant="soft">
            {props.currentHistorySubscription
              ? `${props.currentHistorySubscription.alias} · ${props.historyUnit}`
              : "未加载"}
          </Chip>
        </div>
        {props.chartData.length ? (
          <LineChart data={props.chartData} height={360}>
            <LineChart.Grid vertical={false} />
            <LineChart.XAxis dataKey="time" tickMargin={8} />
            <LineChart.YAxis width={56} />
            <LineChart.Line dataKey="value" dot={false} name={`电量（${props.historyUnit}）`} stroke="var(--color-accent)" strokeWidth={2} type="monotone" />
            <LineChart.Tooltip
              content={({active, label, payload}: any) => {
                if (!active || !payload?.length) return null;
                return (
                  <ChartTooltip>
                    <ChartTooltip.Header>{label}</ChartTooltip.Header>
                    {payload.map((entry: any) => (
                      <ChartTooltip.Item key={String(entry.dataKey)}>
                        <ChartTooltip.Indicator color={entry.color ?? entry.stroke} />
                        <ChartTooltip.Label>{entry.name}</ChartTooltip.Label>
                        <ChartTooltip.Value>{Number(entry.value).toFixed(2)}</ChartTooltip.Value>
                      </ChartTooltip.Item>
                    ))}
                  </ChartTooltip>
                );
              }}
            />
          </LineChart>
        ) : (
          <div className="text-muted flex h-[360px] items-center justify-center rounded-2xl bg-surface-secondary text-sm">
            暂无历史采样
          </div>
        )}
        <p className="text-muted text-sm">{props.historySummary}</p>
      </Card.Content>
    </Card>
  );
}

function DiagnosticsPanel({diagnostics}: {diagnostics: Diagnostic[]}) {
  return (
    <Card className="rounded-2xl">
      <Card.Header>
        <Card.Title>诊断记录</Card.Title>
        <Card.Description>查看插件运行相关的诊断信息与异常提示。</Card.Description>
      </Card.Header>
      <Card.Content>
        <Table variant="secondary">
          <Table.ScrollContainer>
            <Table.Content aria-label="诊断记录" className="min-w-[860px]">
              <Table.Header>
                <Table.Column id="time">时间</Table.Column>
                <Table.Column id="scope">范围</Table.Column>
                <Table.Column id="level">级别</Table.Column>
                <Table.Column id="message" isRowHeader>信息</Table.Column>
              </Table.Header>
              <Table.Body
                items={diagnostics}
                renderEmptyState={() => (
                  <div className="text-muted flex h-24 items-center justify-center text-sm">暂无诊断。</div>
                )}
              >
                {(item) => (
                  <Table.Row id={item.id || `${item.created_at}-${item.scope}-${item.message}`}>
                    <Table.Cell>{formatTime(item.created_at)}</Table.Cell>
                    <Table.Cell><Chip size="sm" variant="soft">{item.scope}</Chip></Table.Cell>
                    <Table.Cell><Chip color={levelColor(item.level)} size="sm" variant="soft">{item.level}</Chip></Table.Cell>
                    <Table.Cell><span className="mono-wrap">{item.message}</span></Table.Cell>
                  </Table.Row>
                )}
              </Table.Body>
            </Table.Content>
          </Table.ScrollContainer>
        </Table>
      </Card.Content>
    </Card>
  );
}

createRoot(document.getElementById("root")!).render(<App />);
