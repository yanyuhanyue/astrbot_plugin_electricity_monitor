import {ChartTooltip} from "@heroui-pro/react/chart-tooltip";
import {LineChart} from "@heroui-pro/react/line-chart";

export interface HistoryChartPoint {
  time: string;
  value: number;
}

export function HistoryChart({
  data,
  height,
  unit,
}: {
  data: Record<string, string | number>[];
  height: number;
  unit: string;
}) {
  return (
    <LineChart data={data} height={height}>
      <LineChart.Grid vertical={false} />
      <LineChart.XAxis dataKey="time" tickMargin={8} />
      <LineChart.YAxis width={56} />
      <LineChart.Line
        dataKey="value"
        dot={false}
        name={`电量（${unit}）`}
        stroke="var(--color-accent)"
        strokeWidth={2}
        type="monotone"
      />
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
  );
}
