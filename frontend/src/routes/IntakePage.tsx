/** Intake (issue #34): upload with feedback + live pipeline status of the
 * last uploads, tracked through the queue detail endpoint. */
import { Alert, Badge, FileInput, Button, Card, Group, Progress, Text, Title } from "@mantine/core";
import { useEffect, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { api } from "~/api/client";
import { QueueItemSchema } from "~/api/schemas";
import { useUpload } from "~/hooks/useUpload";
import type { UploadReceipt } from "~/hooks/useUpload";
import classes from "./IntakePage.module.css";

interface Receipt extends UploadReceipt {
  filename: string;
}

const RECEIPTS_KEY = "invoiceops.intake.receipts";

function loadReceipts(): Receipt[] {
  try {
    const raw = window.localStorage.getItem(RECEIPTS_KEY);
    return raw ? (JSON.parse(raw) as Receipt[]) : [];
  } catch {
    return [];
  }
}

export function IntakePage() {
  const upload = useUpload();
  const navigate = useNavigate();
  const [file, setFile] = useState<File | null>(null);
  const [receipts, setReceipts] = useState<Receipt[]>(loadReceipts);

  useEffect(() => {
    window.localStorage.setItem(RECEIPTS_KEY, JSON.stringify(receipts.slice(0, 8)));
  }, [receipts]);

  const submit = async () => {
    if (!file) return;
    const receipt = await upload.mutateAsync(file).catch(() => null);
    if (receipt) {
      setReceipts((prev) => [{ ...receipt, filename: file.name }, ...prev].slice(0, 8));
      if (!receipt.duplicate) {
        // Straight to the live run — the pipeline is already executing.
        setFile(null);
        navigate(`/runs/${receipt.run_id}`);
        return;
      }
      // Duplicates stay here: the REJECTED badge + toast explain why.
    }
    setFile(null);
  };

  const firstId = receipts[0]?.invoice_id ?? null;
  const status = usePipelineStatus(firstId);

  return (
    <div className={classes.page}>
      <Title order={2}>Intake</Title>

      <Card className={classes.card} withBorder>
        <Title order={4} className={classes.cardTitle}>
          Upload an invoice document
        </Title>
        <Text className={classes.hint}>
          PDF or image; content-hashed on arrival — exact duplicates are rejected, near-duplicates
          surface as DUP_NEAR exceptions.
        </Text>
        <Group className={classes.uploadRow}>
          <FileInput
            placeholder="Choose a file…"
            value={file}
            onChange={setFile}
            accept="application/pdf,image/png,image/jpeg,image/tiff,image/webp"
            className={classes.fileInput}
            data-testid="intake-file"
          />
          <Button
            onClick={submit}
            disabled={!file}
            loading={upload.isPending}
            className={classes.submit}
            data-testid="intake-submit"
          >
            Upload
          </Button>
        </Group>
        {upload.isPending && <Progress className={classes.progress} value={100} />}
      </Card>

      {receipts.length > 0 && (
        <Card className={classes.card} withBorder>
          <Title order={4} className={classes.cardTitle}>
            Recent uploads
          </Title>
          {receipts.map((receipt) => (
            <Group key={receipt.invoice_id} className={classes.receiptRow}>
              <Text className={classes.mono}>{receipt.filename}</Text>
              <Badge
                variant="light"
                className={receipt.duplicate ? classes.badgeRejected : classes.badgeAccepted}
              >
                {receipt.duplicate ? "REJECTED — duplicate" : "ACCEPTED"}
              </Badge>
              <Link to={`/queue/${receipt.invoice_id}`} className={classes.link}>
                invoice #{receipt.invoice_id}
              </Link>
              {receipt.invoice_id === firstId && status && (
                <Badge variant="outline">
                  {status.run?.route
                    ? `${status.run.route} · ${status.run.status}`
                    : status.status}
                </Badge>
              )}
            </Group>
          ))}
        </Card>
      )}

      {upload.isError && (
        <Alert className={classes.alert} title="Upload failed">
          {String(upload.error)}
        </Alert>
      )}
    </div>
  );
}

/** Polls the newest receipt until its run settles; display-only. */
function usePipelineStatus(invoiceId: number | null) {
  return useQuery({
    queryKey: ["invoices", "detail", invoiceId, "intake-status"],
    enabled: invoiceId !== null,
    refetchInterval: (query) => {
      const data = query.state.data as { run?: { route?: string | null } } | undefined;
      return data?.run?.route ? false : 2000;
    },
    queryFn: async () => {
      if (invoiceId === null) throw new Error("no invoice");
      const { data, response } = await api.GET("/v1/invoices/{invoice_id}", {
        params: { path: { invoice_id: invoiceId } },
      });
      if (!response.ok || !data) throw new Error(`status HTTP ${response.status}`);
      return QueueItemSchema.parse(data);
    },
  }).data;
}
