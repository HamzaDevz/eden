// SPDX-FileCopyrightText: Copyright 2026 Eden Emulator Project
// SPDX-License-Identifier: GPL-3.0-or-later

package org.yuzu.yuzu_emu.views

import android.animation.ValueAnimator
import android.content.Context
import android.graphics.Canvas
import android.graphics.Color
import android.graphics.Paint
import android.graphics.Path
import android.graphics.RectF
import android.graphics.Typeface
import android.view.View
import android.view.animation.LinearInterpolator
import java.util.Locale
import kotlin.math.PI
import kotlin.math.min
import kotlin.math.roundToLong
import kotlin.math.sin

/**
 * A points badge the game bridge draws over the game ("badge", such as the Smash points of a card
 * from tools/odin3/smash_record.py --badges): a dark pill side high with its label and the points,
 * against the right edge of the view. On a card it swells when the points change and lets the
 * change rise above it with an arrow, green up or red down; as a row (the results) it slides in,
 * counts up to the new points and keeps the change beside them.
 */
class LevelBadgeView(context: Context) : View(context) {
    private var level = Long.MIN_VALUE
    private var gain = 0L
    private var label = ""
    private var row = false
    private var side = 1f
    private var t = 1f // progress of the change animation, 1 when done

    private val fill = Paint(Paint.ANTI_ALIAS_FLAG).apply { color = Color.argb(225, 14, 16, 28) }
    private val rim = Paint(Paint.ANTI_ALIAS_FLAG).apply { style = Paint.Style.STROKE }
    private val chip = Paint(Paint.ANTI_ALIAS_FLAG)
    private val arrow = Paint(Paint.ANTI_ALIAS_FLAG)
    private val text = Paint(Paint.ANTI_ALIAS_FLAG).apply {
        typeface = Typeface.create(Typeface.DEFAULT, Typeface.BOLD)
    }
    private val outline = Paint(text).apply {
        style = Paint.Style.STROKE
        strokeJoin = Paint.Join.ROUND
        color = Color.argb(230, 0, 0, 0)
    }
    private val box = RectF()
    private val path = Path()
    private var animator: ValueAnimator? = null

    init {
        isClickable = false
        isFocusable = false
    }

    /** Shows level under label, a row on the results or a card's badge, side its pill's height. */
    fun show(level: Long, gain: Long, label: String, row: Boolean, side: Int) {
        if (label != this.label || row != this.row || side.toFloat() != this.side) {
            this.label = label
            this.row = row
            this.side = side.toFloat()
            invalidate()
        }
        if (level == this.level) {
            return
        }
        this.level = level
        this.gain = gain
        if (row || gain != 0L) {
            animator?.cancel()
            animator = ValueAnimator.ofFloat(0f, 1f).apply {
                duration = if (row) 2200 else 3200
                interpolator = LinearInterpolator()
                addUpdateListener {
                    t = it.animatedValue as Float
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

    private fun points(n: Long) = String.format(Locale.FRANCE, "%,d", n)

    private fun change() = when {
        gain > 0 -> "+" + points(gain)
        gain < 0 -> "−" + points(-gain)
        else -> "±0"
    }

    private fun changeColor(alpha: Int) = when {
        gain > 0 -> Color.argb(alpha, 92, 232, 110)
        gain < 0 -> Color.argb(alpha, 255, 86, 76)
        else -> Color.argb(alpha, 190, 190, 200)
    }

    private fun width(s: String, size: Float): Float {
        text.textSize = size
        return text.measureText(s)
    }

    /** s from x on the line through y (its middle), outlined when outlined is not 0. */
    private fun draw(canvas: Canvas, s: String, x: Float, y: Float, size: Float, color: Int, outlined: Float = 0f) {
        text.textSize = size
        val base = y - (text.ascent() + text.descent()) / 2
        if (outlined > 0f) {
            outline.textSize = size
            outline.strokeWidth = size * outlined
            outline.alpha = Color.alpha(color)
            canvas.drawText(s, x, base, outline)
        }
        text.color = color
        canvas.drawText(s, x, base, text)
    }

    /** The arrow of the change, size high, centered on x, y, up for a gain. */
    private fun drawArrow(canvas: Canvas, x: Float, y: Float, size: Float, color: Int) {
        val h = size * 0.5f
        val w = size * 0.6f
        val dir = if (gain > 0) -1f else 1f
        path.reset()
        path.moveTo(x, y + dir * h)
        path.lineTo(x - w, y - dir * h)
        path.lineTo(x + w, y - dir * h)
        path.close()
        arrow.color = color
        canvas.drawPath(path, arrow)
    }

    /** The arrow and change, size high, from x on the line through y; its width. */
    private fun drawChange(canvas: Canvas?, x: Float, y: Float, size: Float, alpha: Int, outlined: Boolean): Float {
        val s = change()
        val a = if (gain != 0L) size * 0.9f else 0f
        if (canvas != null) {
            if (gain != 0L) {
                if (outlined) {
                    drawArrow(canvas, x + a / 2, y, size * 0.82f, Color.argb(alpha, 0, 0, 0))
                }
                drawArrow(canvas, x + a / 2, y, size * 0.62f, changeColor(alpha))
            }
            draw(canvas, s, x + a + size * 0.08f, y, size, changeColor(alpha), if (outlined) 0.18f else 0f)
        }
        return a + size * 0.08f + width(s, size)
    }

    private fun drawPill(canvas: Canvas, left: Float, cy: Float, w: Float) {
        val s = side
        box.set(left, cy - s / 2, left + w, cy + s / 2)
        canvas.drawRoundRect(box, s / 2, s / 2, fill)
        rim.strokeWidth = s * 0.07f
        rim.color = Color.rgb(247, 181, 41)
        canvas.drawRoundRect(box, s / 2, s / 2, rim)
    }

    override fun onDraw(canvas: Canvas) {
        if (row) drawRow(canvas) else drawCard(canvas)
    }

    private fun drawCard(canvas: Canvas) {
        val s = side
        val number = points(level)
        val pad = s * 0.42f
        val gap = if (label.isEmpty()) 0f else s * 0.26f
        var k = 1f
        val content = width(label, s * 0.36f) + gap + width(number, s * 0.56f)
        val room = width - s * 0.25f - 2 * pad
        if (content > room) {
            k = room / content
        }
        val w = content * k + 2 * pad
        val cy = height - s * 0.62f
        val right = width - s * 0.12f
        val cx = right - w / 2

        // A quick swell then back to size over the first sixth of the animation
        val swell = if (t < 1f) 1f + 0.14f * sin(PI * min(t * 6, 1f)).toFloat() else 1f
        canvas.save()
        canvas.scale(swell, swell, cx, cy)
        drawPill(canvas, right - w, cy, w)
        var x = right - w + pad
        if (label.isNotEmpty()) {
            draw(canvas, label, x, cy, s * 0.36f * k, Color.rgb(255, 205, 80))
            x += width(label, s * 0.36f * k) + gap * k
        }
        draw(canvas, number, x, cy, s * 0.56f * k, Color.WHITE)
        canvas.restore()

        if (t < 1f && gain != 0L) {
            val size = s * 0.5f
            val from = cy - s * 0.95f
            val y = from - t * (from - size * 0.7f)
            val alpha = ((1f - t * t) * 255).toInt()
            val cw = drawChange(null, 0f, 0f, size, alpha, true)
            drawChange(canvas, (cx - cw / 2).coerceAtMost(width - cw), y, size, alpha, true)
        }
    }

    private fun drawRow(canvas: Canvas) {
        val s = side
        // Slides in over the first fifth, counts up to the new points over the first two thirds,
        // and the change comes in after a quarter
        val enter = ease(min(t * 5, 1f))
        val count = ease(min(t * 1.5f, 1f))
        val late = ease(((t - 0.25f) * 4).coerceIn(0f, 1f))
        val final = points(level)
        val number = points(level - gain + (gain * count).roundToLong())
        val pad = s * 0.42f
        val gap = s * 0.3f
        val chipPad = s * 0.2f
        val numberSize = s * 0.56f
        val changeSize = s * 0.46f
        val cy = height / 2f

        // Columns the rows share: the name, the points ending where the change starts, which is
        // wide enough for most; a longer name makes its row longer
        val cw = drawChange(null, 0f, 0f, changeSize, 255, false) + 2 * chipPad
        val column = maxOf(cw, changeSize * 0.98f + width(points(88888) + "+", changeSize) + 2 * chipPad)
        val tail = gap + width(final, numberSize) + gap + column + s * 0.14f
        val w = (pad + width(label, s * 0.42f) + tail).coerceIn(s * 7.4f, width - s * 0.1f)
        val right = width - s * 0.05f + (1f - enter) * s * 1.5f
        val left = right - w
        val chipLeft = right - s * 0.14f - column
        val numberRight = chipLeft - gap
        val room = w - pad - tail
        val nameSize = s * 0.42f * (room / width(label, s * 0.42f).coerceAtLeast(1f)).coerceIn(0.4f, 1f)

        canvas.saveLayerAlpha(0f, 0f, width.toFloat(), height.toFloat(), (enter * 255).toInt())
        drawPill(canvas, left, cy, w)
        draw(canvas, label, left + pad, cy, nameSize, Color.rgb(255, 205, 80))
        // The points end at the same place while they count up
        draw(canvas, number, numberRight - width(number, numberSize), cy, numberSize, Color.WHITE)
        if (late > 0f) {
            val alpha = (late * 255).toInt()
            chip.color = changeColor((late * 70).toInt())
            val ch = s * 0.74f
            box.set(chipLeft, cy - ch / 2, chipLeft + cw, cy + ch / 2)
            canvas.drawRoundRect(box, ch / 2, ch / 2, chip)
            // The arrow lands with a small bounce in its direction
            val bounce = if (gain == 0L) 0f else
                (if (gain > 0) -1f else 1f) * s * 0.12f * (1f - late) * sin(PI * late * 2).toFloat()
            drawChange(canvas, chipLeft + chipPad, cy + bounce, changeSize, alpha, false)
        }
        canvas.restore()
    }

    private fun ease(x: Float) = 1f - (1f - x) * (1f - x) * (1f - x)
}
