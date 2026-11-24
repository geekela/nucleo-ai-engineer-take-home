import { Component, type ReactNode } from "react";

export default class ErrorBoundary extends Component<{ children: ReactNode }, { failed: boolean }> {
  state = { failed: false };

  static getDerivedStateFromError() {
    return { failed: true };
  }

  componentDidCatch(error: unknown) {
    console.error("UI error", error);
  }

  render() {
    if (this.state.failed) {
      return (
        <div className="crashed" role="alert">
          <p>The page hit a problem it couldn't recover from. Reload to continue; your uploads are kept by the local server.</p>
          <button type="button" className="primary" onClick={() => window.location.reload()}>
            Reload
          </button>
        </div>
      );
    }
    return this.props.children;
  }
}
