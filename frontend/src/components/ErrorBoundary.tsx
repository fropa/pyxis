import React from "react";

interface State { error: Error | null }

export class ErrorBoundary extends React.Component<
  { children: React.ReactNode },
  State
> {
  state: State = { error: null };

  static getDerivedStateFromError(error: Error): State {
    return { error };
  }

  render() {
    const { error } = this.state;
    if (error) {
      return (
        <div className="min-h-screen bg-[#0d1117] flex items-center justify-center p-8">
          <div className="max-w-xl w-full bg-[#161b22] border border-[#30363d] rounded-xl p-6 shadow-lg">
            <h1 className="text-[16px] font-semibold text-red-400 mb-2">
              Something went wrong
            </h1>
            <p className="text-[13px] text-[#8b949e] mb-4">
              The app crashed. Check the browser console for details.
            </p>
            <pre className="bg-[#0d1117] border border-[#30363d] rounded-lg p-4 text-[11px] text-red-300 font-mono overflow-auto max-h-64 whitespace-pre-wrap">
              {error.message}
              {"\n\n"}
              {error.stack}
            </pre>
            <button
              onClick={() => window.location.reload()}
              className="mt-4 px-4 py-2 bg-[#4f46e5] text-white text-[13px] font-semibold rounded-lg hover:bg-[#4338ca] transition-colors"
            >
              Reload page
            </button>
          </div>
        </div>
      );
    }
    return this.props.children;
  }
}
