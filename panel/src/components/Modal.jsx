import { createPortal } from "react-dom";
import { IcClose } from "./icons.jsx";

// Oyna body'ga chiziladi: sahifa (.content) ichida qolsa uning stacking-context'i tufayli
// fon qoraytirishi chap menyuni qoplamas edi.
export default function Modal({ title, onClose, children, footer, wide }) {
  return createPortal(
    <div className="modal-overlay" onMouseDown={onClose}>
      <div className={`modal${wide ? " wide" : ""}`} onMouseDown={(e) => e.stopPropagation()}>
        <div className="modal-head">
          <div className="title">{title}</div>
          <button className="btn-sq" onClick={onClose} title="Yopish"><IcClose size={16} /></button>
        </div>
        <div className="modal-body">{children}</div>
        {footer && <div className="modal-foot">{footer}</div>}
      </div>
    </div>,
    document.fullscreenElement || document.body,
  );
}
