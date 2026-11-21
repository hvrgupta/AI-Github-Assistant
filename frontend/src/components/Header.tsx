interface HeaderProps {
  onNewChat: () => void;
  disabled: boolean;
}

export function Header({ onNewChat, disabled }: HeaderProps) {
  return (
    <header className="app-header">
      <div className="brand">
        <div className="brand-mark" aria-hidden="true">EA</div>
        <div>
          <p className="brand-name">Enterprise Developer Assistant</p>
          <p className="brand-status">Read-only workspace</p>
        </div>
      </div>
      <button
        className="secondary-button"
        type="button"
        onClick={onNewChat}
        disabled={disabled}
      >
        New Chat
      </button>
    </header>
  );
}
