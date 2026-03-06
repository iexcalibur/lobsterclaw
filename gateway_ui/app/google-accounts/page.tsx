"use client";

import { useEffect, useState, type FormEvent } from "react";
import {
  AlertTriangle,
  CheckCircle2,
  Link2,
  Loader2,
  Mail,
  Plus,
  Trash2,
  Unlink,
} from "lucide-react";
import { api, postJSON } from "@/lib/api";

type GoogleWorkspaceAccount = {
  id: string;
  label: string;
  email_masked: string;
  source: string;
  status: string;
  added_at: string;
  oauth_connected?: boolean;
};

type GoogleWorkspaceResponse = {
  accounts: GoogleWorkspaceAccount[];
  total: number;
};

type AddAccountPayload = {
  label: string;
  email: string;
};

const statusBadge = (status: string) => {
  const normalized = status.toLowerCase();
  if (normalized === "inactive" || normalized === "disabled") {
    return "badge badge-red";
  }
  return "badge badge-green";
};

export default function GoogleAccountsPage() {
  const [accounts, setAccounts] = useState<GoogleWorkspaceAccount[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [message, setMessage] = useState("");
  const [label, setLabel] = useState("");
  const [email, setEmail] = useState("");
  const [saving, setSaving] = useState(false);
  const [deletingId, setDeletingId] = useState<string | null>(null);

  const loadAccounts = async () => {
    setLoading(true);
    setError("");
    try {
      const result = await api<GoogleWorkspaceResponse>(
        "/api/gateway/google-accounts"
      );
      setAccounts(result.accounts || []);
    } catch (err: any) {
      setError(err?.message ?? "Failed to load connected accounts");
    } finally {
      setLoading(false);
    }
  };

  const connectOAuth = async (accountId: string) => {
    setError("");
    try {
      const { authorization_url } = await api<{ authorization_url: string }>(
        `/api/gateway/google-oauth/start?account_id=${encodeURIComponent(accountId)}`
      );
      window.location.href = authorization_url;
    } catch (err: any) {
      setError(err?.message ?? "Failed to start OAuth");
    }
  };

  const disconnectOAuth = async (accountId: string) => {
    if (!window.confirm("Disconnect OAuth? You will need to connect again to use Gmail tools.")) return;
    setError("");
    try {
      await postJSON("/api/gateway/google-oauth/disconnect", { account_id: accountId });
      setMessage("OAuth disconnected.");
      await loadAccounts();
    } catch (err: any) {
      setError(err?.message ?? "Failed to disconnect");
    }
  };

  const addAccount = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    setError("");
    setMessage("");
    if (!label.trim() || !email.trim()) {
      setError("Label and email are required.");
      return;
    }

    const payload: AddAccountPayload = {
      label: label.trim(),
      email: email.trim(),
    };

    setSaving(true);
    try {
      await postJSON<{ ok: boolean; account: GoogleWorkspaceAccount }>(
        "/api/gateway/google-accounts",
        payload
      );
      setLabel("");
      setEmail("");
      setMessage("Account added.");
      await loadAccounts();
    } catch (err: any) {
      setError(err?.message ?? "Failed to add account");
    } finally {
      setSaving(false);
    }
  };

  const removeAccount = async (idOrLabel: string) => {
    if (!idOrLabel) {
      return;
    }
    if (!window.confirm("Remove this account from the registry?")) {
      return;
    }

    setError("");
    setMessage("");
    setDeletingId(idOrLabel);
    try {
      await api(`/api/gateway/google-accounts/${encodeURIComponent(idOrLabel)}`, {
        method: "DELETE",
      });
      setMessage("Account removed.");
      await loadAccounts();
    } catch (err: any) {
      setError(err?.message ?? "Failed to remove account");
    } finally {
      setDeletingId(null);
    }
  };

  useEffect(() => {
    loadAccounts();
  }, []);

  useEffect(() => {
    const params = new URLSearchParams(window.location.search);
    const connected = params.get("oauth_connected");
    const oauthError = params.get("oauth_error");
    if (connected === "1") {
      setMessage("OAuth connected successfully.");
      window.history.replaceState({}, "", "/google-accounts");
      loadAccounts();
    }
    if (oauthError) {
      setError(`OAuth failed: ${oauthError}`);
      window.history.replaceState({}, "", "/google-accounts");
    }
  }, []);

  if (loading) {
    return (
      <div className="card flex items-center gap-2 text-zinc-500 text-sm">
        <Loader2 className="h-4 w-4 animate-spin" />
        <span>Loading connected accounts...</span>
      </div>
    );
  }

  return (
    <div className="space-y-6 fade-in">
      <div>
        <h1 className="text-2xl font-semibold">Google Workspace Accounts</h1>
        <p className="text-sm text-zinc-500 mt-1">
          Read-only account listing with add/remove actions.
        </p>
      </div>

      <div className="card">
        <h2 className="text-sm font-medium text-zinc-400 mb-3">Add account</h2>
        <form
          onSubmit={addAccount}
          className="grid grid-cols-1 gap-3 sm:grid-cols-[1fr_1fr_auto]"
        >
          <input
            value={label}
            onChange={(event) => setLabel(event.target.value)}
            placeholder="Label (e.g. Personal)"
            className="input"
          />
          <input
            value={email}
            onChange={(event) => setEmail(event.target.value)}
            placeholder="Email"
            className="input"
            type="email"
          />
          <button
            type="submit"
            disabled={saving}
            className="btn btn-primary"
          >
            {saving ? (
              <span className="inline-flex items-center gap-2">
                <Loader2 className="h-4 w-4 animate-spin" />
                Adding
              </span>
            ) : (
              <span className="inline-flex items-center gap-2">
                <Plus className="h-4 w-4" />
                Add
              </span>
            )}
          </button>
        </form>
      </div>

      <div className="card">
        <div className="mb-4 flex items-center justify-between gap-2">
          <h2 className="text-sm font-medium text-zinc-400">Authorized Accounts</h2>
          <span className="text-xs text-zinc-500">{accounts.length} total</span>
        </div>

        {error ? (
          <div className="rounded-md border border-red-900/40 bg-red-950/20 p-3 text-sm text-red-300 mb-4 flex items-center gap-2">
            <AlertTriangle className="h-4 w-4" />
            {error}
          </div>
        ) : null}

        {message ? (
          <div className="rounded-md border border-emerald-900/40 bg-emerald-950/20 p-3 text-sm text-emerald-300 mb-4 flex items-center gap-2">
            <CheckCircle2 className="h-4 w-4" />
            {message}
          </div>
        ) : null}

        {accounts.length === 0 ? (
          <p className="text-sm text-zinc-500">No connected accounts yet.</p>
        ) : (
          <div className="space-y-2">
            {accounts.map((account) => (
              <div
                key={account.id}
                className="rounded-lg border border-zinc-800 bg-zinc-900/40 p-3"
              >
                <div className="flex items-start justify-between gap-3">
                  <div className="flex items-start gap-3 min-w-0">
                    <Mail className="h-4 w-4 mt-0.5 text-zinc-400" />
                    <div className="min-w-0">
                      <h3 className="text-sm font-medium text-white">
                        {account.label}
                      </h3>
                      <p className="text-xs text-zinc-500 mt-0.5">
                        {account.email_masked}
                      </p>
                      <p className="text-xs text-zinc-600 mt-1">
                        Source: {account.source} · Added {account.added_at}
                      </p>
                    </div>
                  </div>

                  <div className="flex items-center gap-2 flex-wrap">
                    {account.oauth_connected ? (
                      <span className="badge badge-green inline-flex items-center gap-1">
                        <CheckCircle2 className="h-3 w-3" />
                        Connected
                      </span>
                    ) : (
                      <span className="badge badge-zinc">Not connected</span>
                    )}
                    <span className={statusBadge(account.status)}>
                      {account.status}
                    </span>
                    {account.oauth_connected ? (
                      <button
                        type="button"
                        onClick={() => disconnectOAuth(account.id)}
                        className="btn btn-ghost"
                        title="Disconnect OAuth"
                      >
                        <Unlink className="h-4 w-4" />
                      </button>
                    ) : (
                      <button
                        type="button"
                        onClick={() => connectOAuth(account.id)}
                        className="btn btn-primary btn-sm"
                        title="Connect via Google OAuth"
                      >
                        <span className="inline-flex items-center gap-2">
                          <Link2 className="h-4 w-4" />
                          Connect
                        </span>
                      </button>
                    )}
                    <button
                      type="button"
                      onClick={() => removeAccount(account.id || account.label)}
                      disabled={Boolean(deletingId === account.id)}
                      className="btn btn-ghost"
                    >
                      {deletingId === account.id ? (
                        <Loader2 className="h-4 w-4 animate-spin" />
                      ) : (
                        <span className="inline-flex items-center gap-2">
                          <Trash2 className="h-4 w-4" />
                          Remove
                        </span>
                      )}
                    </button>
                  </div>
                </div>
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}
