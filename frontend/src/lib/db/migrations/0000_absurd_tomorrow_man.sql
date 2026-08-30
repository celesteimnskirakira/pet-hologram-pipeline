CREATE TABLE "generation_tasks" (
	"id" varchar(64) PRIMARY KEY NOT NULL,
	"image_url" text,
	"image_id" varchar(128),
	"status" varchar(24) DEFAULT 'pending' NOT NULL,
	"stage" varchar(32) DEFAULT 'queued' NOT NULL,
	"progress" integer DEFAULT 0 NOT NULL,
	"duration_ms" integer DEFAULT 14000 NOT NULL,
	"video_url" text,
	"error" text,
	"started_at" timestamp DEFAULT now() NOT NULL,
	"updated_at" timestamp DEFAULT now() NOT NULL
);
--> statement-breakpoint
CREATE TABLE "users" (
	"id" varchar(128) PRIMARY KEY NOT NULL,
	"email" varchar(256),
	"name" text,
	"avatar_url" text,
	"created_at" timestamp DEFAULT now() NOT NULL,
	"updated_at" timestamp DEFAULT now() NOT NULL,
	CONSTRAINT "users_email_unique" UNIQUE("email")
);
--> statement-breakpoint
CREATE INDEX "generation_tasks_status_idx" ON "generation_tasks" USING btree ("status");--> statement-breakpoint
CREATE INDEX "generation_tasks_started_at_idx" ON "generation_tasks" USING btree ("started_at");--> statement-breakpoint
CREATE INDEX "users_email_idx" ON "users" USING btree ("email");--> statement-breakpoint
CREATE INDEX "users_created_at_idx" ON "users" USING btree ("created_at");