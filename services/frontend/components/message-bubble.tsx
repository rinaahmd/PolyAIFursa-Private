import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { cn } from "@/lib/utils";
import type { ChatMessage } from "@/lib/types";

export default function MessageBubble({ message }: { message: ChatMessage }) {
  const isUser = message.role === "user";
  return (
    <div className={cn("flex", isUser ? "justify-end" : "justify-start")}>
      <div
        className={cn(
          "max-w-[80%] rounded-2xl px-4 py-3 text-sm shadow-sm",
          isUser
            ? "bg-primary text-primary-foreground rounded-br-sm"
            : "bg-muted text-foreground rounded-bl-sm border border-border/50"
        )}
      >
        {message.image_base64 && (
          <img
            src={`data:image/jpeg;base64,${message.image_base64}`}
            alt="uploaded"
            className="mb-2 max-h-48 rounded-lg object-contain"
          />
        )}
        {isUser ? (
          <p className="whitespace-pre-wrap">{message.content}</p>
        ) : (
          <>
            <div className="prose prose-sm max-w-none dark:prose-invert prose-p:my-1 prose-ul:my-1 prose-li:my-0">
              <ReactMarkdown remarkPlugins={[remarkGfm]}>
                {message.content}
              </ReactMarkdown>
            </div>
            {/* When an edit ran, its result is what the user asked for - the
                boxed detection image behind it (from the detect_objects call
                needed to resolve "the last person on the right" etc.) is an
                internal step, not the answer, so it's hidden whenever a
                processed_image is also present. Only shown on its own for a
                pure detection query with no edit (e.g. "what's in this image"). */}
            {message.annotated_image && !message.processed_image && (
              <img
                src={`data:image/jpeg;base64,${message.annotated_image}`}
                alt="annotated"
                className="mt-2 max-h-48 rounded-lg object-contain"
              />
            )}
            {message.processed_image && (
              <img
                src={`data:image/png;base64,${message.processed_image}`}
                alt="processed"
                className="mt-2 max-h-48 rounded-lg object-contain"
              />
            )}
          </>
        )}
      </div>
    </div>
  );
}
