// SPDX-FileCopyrightText: Copyright 2026 Eden Emulator Project
// SPDX-License-Identifier: GPL-3.0-or-later

package org.yuzu.yuzu_emu.views

import android.animation.ValueAnimator
import android.content.Context
import android.graphics.Canvas
import android.graphics.Color
import android.graphics.LinearGradient
import android.graphics.Paint
import android.graphics.RectF
import android.graphics.Shader
import android.graphics.Typeface
import android.view.View
import android.view.animation.DecelerateInterpolator
import java.util.Locale

/**
 * A points badge the game bridge draws over the game ("badge", such as the Smash points of a card
 * from tools/odin3/smash_record.py --badges): a gold plate with its label (the mode) over the
 * number in the bottom half of the view, which pops and lets "+gain" rise into the top half when
 * the points go up, or "-gain" in red when they go down.
 */
class LevelBadgeView(context: Context) : View(context) {
    private var level = Long.MIN_VALUE
    private var gain = 0L
    private var label = ""
    private var pop = 1f // progress of the change animation, 1 when done

    private val plate = Paint(Paint.ANTI_ALIAS_FLAG)
    private val rim = Paint(Paint.ANTI_ALIAS_FLAG).apply { style = Paint.Style.STROKE }
    private val glow = Paint(Paint.ANTI_ALIAS_FLAG).apply { color = Color.rgb(255, 214, 90) }
    private val text = Paint(Paint.ANTI_ALIAS_FLAG).apply {
        textAlign = Paint.Align.CENTER
        typeface = Typeface.create(Typeface.DEFAULT, Typeface.BOLD)
    }
    private val outline = Paint(text).apply {
        style = Paint.Style.STROKE
        strokeJoin = Paint.Join.ROUND
        color = Color.rgb(60, 30, 0)
    }
    private val box = RectF()
    private var animator: ValueAnimator? = null

    init {
        isClickable = false
        isFocusable = false
    }

    /** Shows level under label, with the animation of gain when it is new and gain is not 0. */
    fun show(level: Long, gain: Long, label: String) {
        if (label != this.label) {
            this.label = label
            invalidate()
        }
        if (level == this.level) {
            return
        }
        this.level = level
        this.gain = gain
        if (gain != 0L) {
            animator?.cancel()
            animator = ValueAnimator.ofFloat(0f, 1f).apply {
                duration = 3500
                interpolator = DecelerateInterpolator()
                addUpdateListener {
                    pop = it.animatedValue as Float
                    invalidate()
                }
                start()
            }
        }
        invalidate()
    }

    override fun onDetachedFromWindow() {
        animator?.cancel()
        super.onDetachedFromWindow()
    }

    /** Text size for s at most size high and width wide. */
    private fun fit(s: String, size: Float, width: Float): Float {
        text.textSize = size
        val w = text.measureText(s)
        return if (w > width) size * width / w else size
    }

    private fun drawOutlined(canvas: Canvas, s: String, x: Float, y: Float, size: Float, color: Int, alpha: Int = 255) {
        text.textSize = size
        outline.textSize = size
        outline.strokeWidth = size * 0.16f
        outline.alpha = alpha
        canvas.drawText(s, x, y, outline)
        text.color = color
        canvas.drawText(s, x, y, text)
        outline.alpha = 255
    }

    override fun onDraw(canvas: Canvas) {
        val w = width.toFloat()
        val h = height / 2f // the plate's half
        val cx = w / 2
        val cy = height - h / 2
        // A quick swell then back to size over the first fifth of the animation
        val swell = if (pop < 1f) 1f + 0.25f * kotlin.math.sin(Math.PI * (pop * 5).coerceAtMost(1f)).toFloat() else 1f
        val pw = w * 0.47f * swell
        val ph = h * 0.42f * swell

        if (pop < 1f) {
            glow.alpha = ((1f - pop) * 150).toInt()
            val g = 1.1f + pop * 0.3f
            box.set(cx - pw * g, cy - ph * g, cx + pw * g, cy + ph * g)
            canvas.drawRoundRect(box, ph * g * 0.5f, ph * g * 0.5f, glow)
        }
        box.set(cx - pw, cy - ph, cx + pw, cy + ph)
        plate.shader = LinearGradient(
            cx, cy - ph, cx, cy + ph,
            intArrayOf(Color.rgb(255, 238, 150), Color.rgb(247, 181, 41), Color.rgb(176, 105, 8)),
            null, Shader.TileMode.CLAMP
        )
        val corner = ph * 0.45f
        canvas.drawRoundRect(box, corner, corner, plate)
        rim.strokeWidth = ph * 0.08f
        rim.color = Color.rgb(90, 50, 0)
        canvas.drawRoundRect(box, corner, corner, rim)

        if (label.isNotEmpty()) {
            text.color = Color.rgb(90, 50, 0)
            text.textSize = fit(label, ph * 0.5f, pw * 1.6f)
            canvas.drawText(label, cx, cy - ph * 0.3f, text)
        }
        val number = String.format(Locale.FRANCE, "%,d", level)
        drawOutlined(canvas, number, cx, cy + ph * 0.62f, fit(number, ph * 0.95f, pw * 1.8f), Color.WHITE)

        if (pop < 1f && gain != 0L) {
            val rise = cy - ph - pop * h * 0.75f
            val alpha = ((1f - pop * pop) * 255).toInt()
            val change = (if (gain > 0) "+" else "") + String.format(Locale.FRANCE, "%,d", gain)
            val color = if (gain > 0) Color.argb(alpha, 120, 255, 90) else Color.argb(alpha, 255, 80, 70)
            drawOutlined(canvas, change, cx, rise, fit(change, h * 0.4f, w * 0.98f), color, alpha)
        }
    }
}
