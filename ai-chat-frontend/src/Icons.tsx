import type { SVGProps } from "react";

type IconProps = SVGProps<SVGSVGElement> & { size?: number };

function StrokeIcon({ size = 16, children, ...props }: IconProps) {
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 16 16"
      fill="none"
      stroke="currentColor"
      strokeWidth={1.4}
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
      focusable="false"
      {...props}
    >
      {children}
    </svg>
  );
}

/** 品牌标记：圆角方块里的对话气泡 */
export function BrandMark({ size = 22, ...props }: IconProps) {
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" aria-hidden="true" focusable="false" {...props}>
      <rect width="24" height="24" rx="7" fill="#4d6bfe" />
      <path
        d="M12 6.6c-3.3 0-6 2.2-6 4.8 0 1.5.9 2.8 2.3 3.6l-.5 2.1 2.5-1.2c.5.1 1.1.1 1.7.1 3.3 0 6-2.1 6-4.7s-2.7-4.7-6-4.7Z"
        fill="#fff"
      />
    </svg>
  );
}

/** 侧栏收起/展开 */
export function PanelLeftIcon(props: IconProps) {
  return (
    <StrokeIcon {...props}>
      <rect x="2.2" y="3" width="11.6" height="10" rx="2.2" />
      <path d="M6.4 3v10" />
    </StrokeIcon>
  );
}

/** 新对话 */
export function EditIcon(props: IconProps) {
  return (
    <StrokeIcon {...props}>
      <path d="M11.3 2.6 13.4 4.7 6.2 12l-2.9.7.7-2.9 7.3-7.2Z" />
      <path d="M2.6 14.2h6.2" />
    </StrokeIcon>
  );
}

/** 搜索 */
export function SearchIcon(props: IconProps) {
  return (
    <StrokeIcon {...props}>
      <circle cx="6.9" cy="6.9" r="4.2" />
      <path d="M10 10l3.4 3.4" />
    </StrokeIcon>
  );
}

/** 联网搜索：地球 */
export function GlobeIcon(props: IconProps) {
  return (
    <StrokeIcon {...props}>
      <circle cx="8" cy="8" r="5.6" />
      <path d="M2.4 8h11.2" />
      <path d="M8 2.4c1.7 1.5 2.6 3.5 2.6 5.6S9.7 12.1 8 13.6C6.3 12.1 5.4 10.1 5.4 8S6.3 3.9 8 2.4Z" />
    </StrokeIcon>
  );
}

/** 退出登录 */
export function LogoutIcon(props: IconProps) {
  return (
    <StrokeIcon {...props}>
      <path d="M9.6 2.8H12a1.6 1.6 0 0 1 1.6 1.6v7.2A1.6 1.6 0 0 1 12 13.2H9.6" />
      <path d="M6.6 8H2.4M4.6 5.6 2.2 8l2.4 2.4" />
    </StrokeIcon>
  );
}

/** 发送 */
export function ArrowUpIcon({ size = 16, ...props }: IconProps) {
  return (
    <StrokeIcon size={size} strokeWidth={1.9} {...props}>
      <path d="M8 12.6V3.6M4.4 7.1 8 3.5l3.6 3.6" />
    </StrokeIcon>
  );
}

/** 停止生成 */
export function StopIcon({ size = 16, ...props }: IconProps) {
  return (
    <svg width={size} height={size} viewBox="0 0 16 16" aria-hidden="true" focusable="false" {...props}>
      <rect x="4.6" y="4.6" width="6.8" height="6.8" rx="1.8" fill="currentColor" />
    </svg>
  );
}

/** 账号保存提示 */
export function LockIcon(props: IconProps) {
  return (
    <StrokeIcon {...props}>
      <rect x="3.4" y="6.9" width="9.2" height="6.4" rx="1.9" />
      <path d="M5.7 6.9V5.2a2.3 2.3 0 0 1 4.6 0v1.7" />
    </StrokeIcon>
  );
}

/** 模型标签 */
export function SparkIcon(props: IconProps) {
  return (
    <StrokeIcon {...props}>
      <path d="M8 2.4l1.5 3.6L13 7.4l-3.5 1.4L8 12.5 6.5 8.8 3 7.4l3.5-1.4L8 2.4Z" />
    </StrokeIcon>
  );
}

/** token 用量标签：累计计数 */
export function TokenIcon(props: IconProps) {
  return (
    <StrokeIcon {...props}>
      <ellipse cx="8" cy="4.3" rx="5" ry="2.1" />
      <path d="M3 4.3v3.4c0 1.16 2.24 2.1 5 2.1s5-.94 5-2.1V4.3" />
      <path d="M3 7.7v3.4c0 1.16 2.24 2.1 5 2.1s5-.94 5-2.1V7.7" />
    </StrokeIcon>
  );
}

/** 关闭浮层 */
export function CloseIcon(props: IconProps) {
  return (
    <StrokeIcon {...props}>
      <path d="M4 4l8 8M12 4l-8 8" />
    </StrokeIcon>
  );
}

/** 展开指示：右箭头 */
export function ChevronRightIcon(props: IconProps) {
  return (
    <StrokeIcon {...props}>
      <path d="M6 3.5 10.5 8 6 12.5" />
    </StrokeIcon>
  );
}
