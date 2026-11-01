// SPDX-FileCopyrightText: Copyright 2026 Eden Emulator Project
// SPDX-License-Identifier: GPL-3.0-or-later

package org.yuzu.yuzu_emu.views

import android.animation.ValueAnimator
import android.content.Context
import android.graphics.Canvas
import android.graphics.Color
import android.graphics.LinearGradient
import android.graphics.Paint
import android.graphics.Shader
import android.graphics.Typeface
import android.view.View
import android.view.animation.DecelerateInterpolator

/**
 * A points badge the game bridge draws over the game ("badge", such as the Smash points of a card
 * from tools/odin3/smash_record.py --badges): a gold medal in the bottom half of the view, which
 * pops and lets "+gain" rise into the top half when the points go up, or "-gain" in red when
 * they go down.
 */
class LevelBadgeView(context: Context) : View(context) {
    private var level = Int.MIN_VALUE
    private var gain = 0
    private var pop = 1f // progress of the level-up animation, 1 when done

    private val medal = Paint(Paint.ANTI_ALIAS_FLAG)
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
    private var animator: ValueAnimator? = null

    init {
        isClickable = false
        isFocusable = false
    }

    /** Shows level, with the animation of gain when it is new and gain is not 0. */
    fun show(level: Int, gain: Int) {
        if (level == this.level) {
            return
        }
        this.level = level
        this.gain = gain
        if (gain != 0) {
            animator?.cancel()
            animator = ValueAnimator.ofFloat(0f, 1f).apply {
                duration = 2200
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

    override fun onDraw(canvas: Canvas) {
        val s = width.toFloat()
        val cx = s / 2
        val cy = height - s / 2
        // A quick swell then back to size over the first third of the animation
        val swell = if (pop < 1f) 1f + 0.35f * kotlin.math.sin(Math.PI * (pop * 3).coerceAtMost(1f)).toFloat() else 1f
        val r = s * 0.42f * swell

        if (pop < 1f) {
            glow.alpha = ((1f - pop) * 160).toInt()
            canvas.drawCircle(cx, cy, r * (1.15f + pop * 0.5f), glow)
        }
        medal.shader = LinearGradient(
            cx, cy - r, cx, cy + r,
            intArrayOf(Color.rgb(255, 238, 150), Color.rgb(247, 181, 41), Color.rgb(176, 105, 8)),
            null, Shader.TileMode.CLAMP
        )
        canvas.drawCircle(cx, cy, r, medal)
        rim.strokeWidth = r * 0.1f
        rim.color = Color.rgb(90, 50, 0)
        canvas.drawCircle(cx, cy, r, rim)
        rim.strokeWidth = r * 0.04f
        rim.color = Color.argb(170, 255, 250, 210)
        canvas.drawCircle(cx, cy, r * 0.8f, rim)

        text.color = Color.rgb(90, 50, 0)
        text.textSize = r * 0.32f
        canvas.drawText("PTS", cx, cy - r * 0.3f, text)
        val number = level.toString()
        text.textSize = r * when {
            number.length > 3 -> 0.5f
            number.length > 2 -> 0.62f
            else -> 0.78f
        }
        outline.textSize = text.textSize
        outline.strokeWidth = text.textSize * 0.14f
        val baseline = cy + r * 0.5f
        canvas.drawText(number, cx, baseline, outline)
        text.color = Color.WHITE
        canvas.drawText(number, cx, baseline, text)

        if (pop < 1f && gain != 0) {
            val rise = cy - r - pop * (height - s) * 0.9f
            val alpha = ((1f - pop * pop) * 255).toInt()
            text.textSize = s * 0.42f
            outline.textSize = text.textSize
            outline.strokeWidth = text.textSize * 0.14f
            outline.alpha = alpha
            val label = if (gain > 0) "+$gain" else "$gain"
            text.color = if (gain > 0) Color.argb(alpha, 120, 255, 90) else Color.argb(alpha, 255, 80, 70)
            canvas.drawText(label, cx, rise, outline)
            canvas.drawText(label, cx, rise, text)
            outline.alpha = 255
        }
    }
}
