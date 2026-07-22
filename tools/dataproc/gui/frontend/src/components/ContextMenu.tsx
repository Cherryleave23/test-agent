import React from "react";

interface MenuItem {
  label: string;
  action: () => void;
  danger?: boolean;
}

interface Props {
  visible: boolean;
  x: number;
  y: number;
  items: MenuItem[];
  onClose: () => void;
}

export default function ContextMenu({ visible, x, y, items, onClose }: Props) {
  if (!visible) return null;

  return (
    <>
      <div className="contextmenu-overlay" onClick={onClose} />
      <div
        className="contextmenu"
        style={{ left: x, top: y }}
        onClick={(e) => e.stopPropagation()}
      >
        {items.map((item, i) => (
          <button
            key={i}
            className={`contextmenu-item ${item.danger ? "danger" : ""}`}
            onClick={() => {
              item.action();
              onClose();
            }}
          >
            {item.label}
          </button>
        ))}
      </div>
    </>
  );
}
